from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
import pymysql
import os
import pdfplumber
import re
import io
from datetime import datetime, date
import traceback

app = Flask(__name__)
CORS(app)

# Diretório raiz do projeto (um nível acima de api/)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── Páginas HTML ──────────────────────────────────────────────────────────────

@app.route('/')
def portal():
    return send_from_directory(BASE_DIR, 'plataforma-pcp.html')

@app.route('/pcp')
def pcp():
    return send_from_directory(BASE_DIR, 'modulo-pcp.html')

# ── Conexão com o banco ───────────────────────────────────────────────────────
# Suporta variáveis do Railway (MYSQLHOST, MYSQLUSER...) e variáveis personalizadas (MYSQL_HOST...)

def get_db():
    host     = os.environ.get('MYSQLHOST')     or os.environ.get('MYSQL_HOST',     'localhost')
    port     = int(os.environ.get('MYSQLPORT') or os.environ.get('MYSQL_PORT',     3306))
    user     = os.environ.get('MYSQLUSER')     or os.environ.get('MYSQL_USER',     'root')
    password = os.environ.get('MYSQLPASSWORD') or os.environ.get('MYSQL_PASSWORD', '')
    database = os.environ.get('MYSQLDATABASE') or os.environ.get('MYSQL_DATABASE', 'confiance_pcp')
    return pymysql.connect(
        host=host, port=port, user=user, password=password,
        database=database, charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_status_counts(pedido_id, db):
    with db.cursor() as cur:
        cur.execute("""
            SELECT status, COUNT(*) as cnt
            FROM itens_pedido WHERE pedido_id=%s GROUP BY status
        """, (pedido_id,))
        rows = cur.fetchall()
    counts = {'Não iniciado': 0, 'Em Produção': 0, 'Pausado': 0, 'Comprado': 0, 'Finalizado': 0}
    for row in rows:
        if row['status'] in counts:
            counts[row['status']] = row['cnt']
    return counts

def str_date(val):
    if val is None:
        return ''
    if isinstance(val, (date, datetime)):
        return val.strftime('%Y-%m-%d')
    return str(val)

def pedido_para_dict(p, db):
    sc = get_status_counts(p['id'], db)
    total_itens = sum(sc.values())
    return {
        'id': p['id'],
        'numero_pedido': p['numero_pedido'],
        'cliente': p['cliente'] or '',
        'estado': p['estado'] or '',
        'segmento': p['segmento'] or '',
        'origem_vendas': p['origem_vendas'] or '',
        'data_entrega': str_date(p['data_entrega']),
        'prev_liberacao': str_date(p['prev_liberacao']),
        'prev_faturamento': p['prev_faturamento'] or '',
        'prev_expedicao': p['prev_expedicao'] or '',
        'valor_total': float(p['valor_total'] or 0),
        'total_volumes': int(p['total_volumes'] or 0),
        'liberado': bool(p['liberado']),
        'status_liberado': p['status_liberado'] or '',
        'num_nf': p['num_nf'] or '',
        'transportadora': p['transportadora'] or '',
        'nf_pdf_nome': p['nf_pdf_nome'] or '',
        'status_counts': sc,
        'total_itens': total_itens,
    }

# ── Rotas de leitura ──────────────────────────────────────────────────────────

@app.route('/api/pcp/pedidos')
def listar_pedidos():
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("""
                SELECT * FROM pedidos
                WHERE finalizado=0 AND liberado=0
                ORDER BY data_entrega ASC
            """)
            pedidos = cur.fetchall()
        return jsonify([pedido_para_dict(p, db) for p in pedidos])
    except Exception as e:
        return jsonify({'erro': str(e)}), 500
    finally:
        db.close()

@app.route('/api/pcp/pedido/<int:pid>', methods=['GET'])
def get_pedido(pid):
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("SELECT * FROM pedidos WHERE id=%s", (pid,))
            p = cur.fetchone()
            if not p:
                return jsonify({'erro': 'Pedido não encontrado'}), 404
            cur.execute("SELECT * FROM itens_pedido WHERE pedido_id=%s ORDER BY id", (pid,))
            itens = cur.fetchall()

        d = pedido_para_dict(p, db)
        d.update({
            'data_emissao': str_date(p['data_emissao']),
            'frete': p['frete'] or '',
            'obs_gerais': p['obs_gerais'] or '',
            'data_liberacao': str_date(p['data_liberacao']),
            'data_faturamento': str_date(p['data_faturamento']),
            'data_entrega_real': str_date(p['data_entrega_real']),
            'volumes_resumo': [],
        })

        itens_list = [{
            'id': i['id'],
            'codigo': i['codigo'] or '',
            'derivacao': i['derivacao'] or '',
            'descricao': i['descricao'] or '',
            'quantidade': float(i['quantidade'] or 0),
            'vlr_unitario': float(i['vlr_unitario'] or 0),
            'vlr_bruto': float(i['vlr_bruto'] or 0),
            'data_entrega': str_date(i['data_entrega']),
            'status': i['status'] or 'Não iniciado',
            'observacao': i['observacao'] or '',
        } for i in itens]

        return jsonify({'pedido': d, 'itens': itens_list})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500
    finally:
        db.close()

@app.route('/api/pcp/planejamento')
def planejamento():
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("""
                SELECT i.id as item_id, i.codigo, i.derivacao, i.descricao,
                       i.quantidade, i.data_entrega, i.status, i.observacao,
                       p.numero_pedido
                FROM itens_pedido i
                JOIN pedidos p ON i.pedido_id = p.id
                WHERE p.finalizado=0
                ORDER BY i.data_entrega ASC, p.numero_pedido ASC
            """)
            rows = cur.fetchall()
        return jsonify([{
            'item_id': r['item_id'],
            'numero_pedido': r['numero_pedido'],
            'codigo': r['codigo'] or '',
            'derivacao': r['derivacao'] or '',
            'descricao': r['descricao'] or '',
            'quantidade': float(r['quantidade'] or 0),
            'data_entrega': str_date(r['data_entrega']),
            'status': r['status'] or 'Não iniciado',
            'observacao': r['observacao'] or '',
        } for r in rows])
    except Exception as e:
        return jsonify({'erro': str(e)}), 500
    finally:
        db.close()

@app.route('/api/pcp/liberados')
def liberados():
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("""
                SELECT * FROM pedidos
                WHERE liberado=1 AND finalizado=0
                ORDER BY prev_liberacao ASC
            """)
            pedidos = cur.fetchall()
        return jsonify([pedido_para_dict(p, db) for p in pedidos])
    except Exception as e:
        return jsonify({'erro': str(e)}), 500
    finally:
        db.close()

@app.route('/api/pcp/historico')
def historico():
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("""
                SELECT * FROM pedidos WHERE finalizado=1
                ORDER BY finalizado_em DESC
            """)
            pedidos = cur.fetchall()
        return jsonify([{
            'id': p['id'],
            'numero_pedido': p['numero_pedido'],
            'cliente': p['cliente'] or '',
            'estado': p['estado'] or '',
            'origem_vendas': p['origem_vendas'] or '',
            'data_entrega': str_date(p['data_entrega']),
            'total_volumes': int(p['total_volumes'] or 0),
            'valor_total': float(p['valor_total'] or 0),
            'finalizado_em': str(p['finalizado_em']) if p['finalizado_em'] else '',
        } for p in pedidos])
    except Exception as e:
        return jsonify({'erro': str(e)}), 500
    finally:
        db.close()

@app.route('/api/pcp/posicao')
def posicao():
    data_str = request.args.get('data', '')
    if not data_str:
        return jsonify([])
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("""
                SELECT * FROM pedidos
                WHERE finalizado=0
                  AND DATE(criado_em) <= %s
                  AND (data_entrega >= %s OR data_entrega IS NULL OR data_entrega = '')
                ORDER BY data_entrega ASC
            """, (data_str, data_str))
            pedidos = cur.fetchall()
        return jsonify([pedido_para_dict(p, db) for p in pedidos])
    except Exception as e:
        return jsonify({'erro': str(e)}), 500
    finally:
        db.close()

# ── Rotas de escrita ──────────────────────────────────────────────────────────

@app.route('/api/pcp/pedido/<int:pid>', methods=['DELETE'])
def deletar_pedido(pid):
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("DELETE FROM pedidos WHERE id=%s", (pid,))
        db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500
    finally:
        db.close()

@app.route('/api/pcp/item/<int:iid>/status', methods=['PATCH'])
def update_status(iid):
    data = request.get_json()
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("UPDATE itens_pedido SET status=%s WHERE id=%s", (data['status'], iid))
        db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500
    finally:
        db.close()

@app.route('/api/pcp/item/<int:iid>/observacao', methods=['PATCH'])
def update_observacao(iid):
    data = request.get_json()
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("UPDATE itens_pedido SET observacao=%s WHERE id=%s", (data['observacao'], iid))
        db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500
    finally:
        db.close()

@app.route('/api/pcp/pedido/<int:pid>/finalizar', methods=['PATCH'])
def finalizar(pid):
    data = request.get_json()
    acao = data.get('acao', 'liberar')
    db = get_db()
    try:
        with db.cursor() as cur:
            if acao == 'liberar':
                cur.execute("""
                    UPDATE pedidos SET liberado=1,
                    status_liberado='Aguardando faturamento',
                    data_liberacao=%s WHERE id=%s
                """, (date.today().isoformat(), pid))
            elif acao == 'finalizar_liberado':
                cur.execute("""
                    UPDATE pedidos SET finalizado=1,
                    finalizado_em=%s WHERE id=%s
                """, (datetime.now().strftime('%d/%m/%Y %H:%M'), pid))
            elif acao == 'restaurar_liberado':
                cur.execute("""
                    UPDATE pedidos SET finalizado=0, finalizado_em=NULL WHERE id=%s
                """, (pid,))
        db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500
    finally:
        db.close()

@app.route('/api/pcp/pedido/<int:pid>/previsoes', methods=['PATCH'])
def update_previsoes(pid):
    data = request.get_json()
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("""
                UPDATE pedidos
                SET prev_liberacao=%s, prev_faturamento=%s, prev_expedicao=%s
                WHERE id=%s
            """, (data.get('prev_liberacao'), data.get('prev_faturamento'),
                  data.get('prev_expedicao'), pid))
        db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500
    finally:
        db.close()

@app.route('/api/pcp/pedido/<int:pid>/segmento', methods=['PATCH'])
def update_segmento(pid):
    data = request.get_json()
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("UPDATE pedidos SET segmento=%s WHERE id=%s",
                        (data.get('segmento'), pid))
        db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500
    finally:
        db.close()

@app.route('/api/pcp/pedido/<int:pid>/liberado_info', methods=['PATCH'])
def update_liberado_info(pid):
    data = request.get_json()
    db = get_db()
    try:
        campos_permitidos = {
            'status_liberado', 'data_liberacao', 'data_faturamento',
            'num_nf', 'transportadora', 'data_entrega_real'
        }
        updates = {k: v for k, v in data.items() if k in campos_permitidos}
        if updates:
            set_clause = ', '.join(f"`{k}`=%s" for k in updates)
            valores = list(updates.values()) + [pid]
            with db.cursor() as cur:
                cur.execute(f"UPDATE pedidos SET {set_clause} WHERE id=%s", valores)
            db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500
    finally:
        db.close()

@app.route('/api/pcp/pedido/<int:pid>/nf_pdf', methods=['POST'])
def upload_nf_pdf(pid):
    if 'pdf' not in request.files:
        return jsonify({'erro': 'Nenhum arquivo enviado'}), 400
    f = request.files['pdf']
    pdf_bytes = f.read()
    nome = f.filename
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("""
                UPDATE pedidos SET nf_pdf=%s, nf_pdf_nome=%s,
                status_liberado='Faturado' WHERE id=%s
            """, (pdf_bytes, nome, pid))
        db.commit()
        return jsonify({'nome': nome})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500
    finally:
        db.close()

@app.route('/api/pcp/pedido/<int:pid>/nf_pdf', methods=['GET'])
def download_nf_pdf(pid):
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("SELECT nf_pdf, nf_pdf_nome FROM pedidos WHERE id=%s", (pid,))
            row = cur.fetchone()
        if not row or not row['nf_pdf']:
            return jsonify({'erro': 'PDF não encontrado'}), 404
        return send_file(
            io.BytesIO(row['nf_pdf']),
            download_name=row['nf_pdf_nome'] or 'nf.pdf',
            mimetype='application/pdf'
        )
    except Exception as e:
        return jsonify({'erro': str(e)}), 500
    finally:
        db.close()

# ── Importação de PDF ─────────────────────────────────────────────────────────

@app.route('/api/pcp/analisar', methods=['POST'])
def analisar_pdf():
    if 'pdf' not in request.files:
        return jsonify({'erro': 'Nenhum arquivo enviado'}), 400

    f = request.files['pdf']
    pdf_bytes = f.read()

    try:
        dados = extrair_dados_pdf(pdf_bytes)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'erro': f'Erro ao processar PDF: {str(e)}'}), 500

    if not dados.get('numero_pedido'):
        return jsonify({'erro': 'Não foi possível identificar o número do pedido no PDF. Verifique o formato do arquivo.'}), 400

    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("SELECT id FROM pedidos WHERE numero_pedido=%s", (dados['numero_pedido'],))
            if cur.fetchone():
                return jsonify({'erro': f'Pedido {dados["numero_pedido"]} já existe no sistema'}), 409

        valor_total = sum(i.get('vlr_bruto', 0) for i in dados.get('itens', []))

        with db.cursor() as cur:
            cur.execute("""
                INSERT INTO pedidos
                (numero_pedido, cliente, estado, data_emissao, data_entrega,
                 origem_vendas, frete, obs_gerais, valor_total, total_volumes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0)
            """, (
                dados['numero_pedido'],
                dados.get('cliente', ''),
                dados.get('estado', ''),
                dados.get('data_emissao', ''),
                dados.get('data_entrega', ''),
                dados.get('origem_vendas', ''),
                dados.get('frete', ''),
                dados.get('obs_gerais', ''),
                valor_total,
            ))
            pedido_id = cur.lastrowid

            for item in dados.get('itens', []):
                cur.execute("""
                    INSERT INTO itens_pedido
                    (pedido_id, codigo, derivacao, descricao,
                     quantidade, vlr_unitario, vlr_bruto, data_entrega, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Não iniciado')
                """, (
                    pedido_id,
                    item.get('codigo', ''),
                    item.get('derivacao', ''),
                    item.get('descricao', ''),
                    item.get('quantidade', 0),
                    item.get('vlr_unitario', 0),
                    item.get('vlr_bruto', 0),
                    dados.get('data_entrega', ''),
                ))

        db.commit()

        with db.cursor() as cur:
            cur.execute("SELECT COUNT(*) as cnt FROM itens_pedido WHERE pedido_id=%s", (pedido_id,))
            total = cur.fetchone()['cnt']

        return jsonify({
            'pedido_id': pedido_id,
            'numero_pedido': dados['numero_pedido'],
            'total_itens': total
        })
    except Exception as e:
        db.rollback()
        traceback.print_exc()
        return jsonify({'erro': str(e)}), 500
    finally:
        db.close()


def extrair_dados_pdf(pdf_bytes: bytes) -> dict:
    """
    Extrai dados de um PDF de Pedido de Venda (Confiance Medical).
    Adapte os padrões de regex conforme o layout real do seu PDF.
    """
    texto = ''
    tabelas = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            texto += (page.extract_text() or '') + '\n'
            for t in (page.extract_tables() or []):
                tabelas.append(t)

    dados = {
        'numero_pedido': None,
        'cliente': '',
        'estado': '',
        'data_emissao': '',
        'data_entrega': '',
        'origem_vendas': '',
        'frete': '',
        'obs_gerais': '',
        'itens': []
    }

    # ── Número do pedido ──────────────────────────────────────────────────────
    # Padrões: "PV 99810", "Pedido: 99810", "N° 99810"
    for pattern in [
        r'PV\s*[:\-]?\s*(\d{4,6})',
        r'(?:Pedido|N[º°o]?\s*(?:do\s+)?Pedido)[:\s#–\-]*(\d{4,6})',
        r'(?:Número|Numero)[:\s]*(\d{4,6})',
    ]:
        m = re.search(pattern, texto, re.IGNORECASE)
        if m:
            dados['numero_pedido'] = m.group(1).strip()
            break

    # ── Cliente ───────────────────────────────────────────────────────────────
    for pattern in [
        r'(?:Cliente|Razão\s+Social|Empresa)[:\s]+([^\n\r]{3,80})',
        r'(?:Para|Destinatário)[:\s]+([^\n\r]{3,80})',
    ]:
        m = re.search(pattern, texto, re.IGNORECASE)
        if m:
            dados['cliente'] = m.group(1).strip()
            break

    # ── Estado (UF) ───────────────────────────────────────────────────────────
    UFS = {'AC','AL','AP','AM','BA','CE','DF','ES','GO','MA','MT','MS',
           'MG','PA','PB','PR','PE','PI','RJ','RN','RS','RO','RR','SC','SP','SE','TO'}
    for uf in re.findall(r'\b([A-Z]{2})\b', texto):
        if uf in UFS:
            dados['estado'] = uf
            break

    # ── Datas ─────────────────────────────────────────────────────────────────
    datas = re.findall(r'\d{2}/\d{2}/\d{4}', texto)
    if datas:
        dados['data_emissao'] = datas[0]
    if len(datas) >= 2:
        dados['data_entrega'] = datas[-1]

    # ── Frete ─────────────────────────────────────────────────────────────────
    m = re.search(r'Frete[:\s]+([^\n\r]{2,50})', texto, re.IGNORECASE)
    if m:
        dados['frete'] = m.group(1).strip()

    # ── Origem de vendas ──────────────────────────────────────────────────────
    m = re.search(r'(?:Origem|Vendedor|Representante)[:\s]+([^\n\r]{2,60})', texto, re.IGNORECASE)
    if m:
        dados['origem_vendas'] = m.group(1).strip()

    # ── Observações ───────────────────────────────────────────────────────────
    m = re.search(r'(?:Obs(?:ervações?)?|Informações?\s+Adicionais)[:\s]+([^\n]{5,300})', texto, re.IGNORECASE)
    if m:
        dados['obs_gerais'] = m.group(1).strip()

    # ── Itens — extração via tabelas ──────────────────────────────────────────
    itens = []
    for tabela in tabelas:
        for linha in tabela:
            if not linha or len(linha) < 3:
                continue
            # Tenta identificar linhas de item pelo código de produto
            codigo_raw = str(linha[0] or '').strip()
            if not re.match(r'^[A-Z0-9][\w\-\.]{1,19}$', codigo_raw):
                continue  # não parece um código de produto
            try:
                # Tenta extrair derivação (campo opcional)
                derivacao = ''
                descricao_idx = 1
                if len(linha) >= 5:
                    # Se a segunda coluna for curta pode ser derivação
                    col1 = str(linha[1] or '').strip()
                    if re.match(r'^\d{2}$', col1):
                        derivacao = col1
                        descricao_idx = 2

                descricao = str(linha[descricao_idx] or '').strip()
                if not descricao or len(descricao) < 3:
                    continue

                # Quantidade: procurar número nos campos restantes
                qtd = 0
                vlr_unit = 0
                vlr_bruto = 0
                nums = []
                for cell in linha[descricao_idx+1:]:
                    raw = re.sub(r'[^\d,\.]', '', str(cell or ''))
                    raw = raw.replace('.', '').replace(',', '.')
                    try:
                        nums.append(float(raw))
                    except:
                        pass

                if len(nums) >= 1:
                    qtd = nums[0]
                if len(nums) >= 2:
                    vlr_unit = nums[-2] if len(nums) >= 3 else nums[1]
                if len(nums) >= 2:
                    vlr_bruto = nums[-1]

                if qtd > 0 and descricao:
                    itens.append({
                        'codigo': codigo_raw,
                        'derivacao': derivacao,
                        'descricao': descricao,
                        'quantidade': qtd,
                        'vlr_unitario': vlr_unit,
                        'vlr_bruto': vlr_bruto or (qtd * vlr_unit),
                    })
            except Exception:
                continue

    # ── Fallback: extração via regex no texto corrido ─────────────────────────
    if not itens:
        # Padrão genérico: CODIGO  Descrição  Qtd  Vlr.Unit  Vlr.Bruto
        pattern = re.compile(
            r'^([A-Z0-9][\w\-\.]{2,18})\s+'   # código
            r'(.{5,60}?)\s+'                   # descrição
            r'(\d+(?:[.,]\d+)?)\s+'            # quantidade
            r'[\d.,]+\s+'                       # vlr unit (pode ser ignorado)
            r'([\d.,]+)',                        # vlr bruto
            re.MULTILINE
        )
        for m in pattern.finditer(texto):
            try:
                qtd = float(m.group(3).replace(',', '.'))
                bruto_raw = m.group(4).replace('.', '').replace(',', '.')
                bruto = float(bruto_raw)
                itens.append({
                    'codigo': m.group(1).strip(),
                    'derivacao': '',
                    'descricao': m.group(2).strip(),
                    'quantidade': qtd,
                    'vlr_unitario': round(bruto / qtd, 2) if qtd else 0,
                    'vlr_bruto': bruto,
                })
            except Exception:
                continue

    dados['itens'] = itens
    return dados


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
