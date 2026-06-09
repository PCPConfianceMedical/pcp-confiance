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


def num_br(s: str) -> float:
    """Converte número brasileiro (1.234,56) para float."""
    return float(s.replace('.', '').replace(',', '.'))

def extrair_dados_pdf(pdf_bytes: bytes) -> dict:
    """
    Parser específico para Pedidos de Venda da Confiance Medical.
    Formato: DOCUMENTO.: PEDIDO.: 99.810 | itens com código de transação (90155/90175)
    """
    from collections import Counter

    texto = ''
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            texto += (page.extract_text() or '') + '\n'

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
    # Formato: "PEDIDO.: 99.810" — o ponto é separador de milhar, remover
    m = re.search(r'PEDIDO\.[:\s]+(\d[\d.]+)', texto, re.IGNORECASE)
    if m:
        dados['numero_pedido'] = m.group(1).replace('.', '')
    else:
        # Fallback: qualquer padrão de número de pedido
        m = re.search(r'(?:pedido|PV)[^\d]*(\d{4,6})', texto, re.IGNORECASE)
        if m:
            dados['numero_pedido'] = m.group(1)

    # ── Cliente ───────────────────────────────────────────────────────────────
    # Formato: "Cliente.......: 2.585 -LIGA NORTE RIOGRANDENSE CONTRA O CANCER"
    m = re.search(r'Cliente[.\s]+:\s*[\d.]+ -(.+?)(?:\n|Endere)', texto, re.IGNORECASE)
    if m:
        dados['cliente'] = m.group(1).strip()
    else:
        m = re.search(r'Cliente[.\s]+:\s*(.+?)(?:\n|Endere)', texto, re.IGNORECASE)
        if m:
            dados['cliente'] = re.sub(r'^\d[\d.]* -?', '', m.group(1)).strip()

    # ── Estado do CLIENTE (segunda ocorrência de "Estado") ────────────────────
    # A empresa é RJ; o cliente pode ser de outro estado
    UFS = {'AC','AL','AP','AM','BA','CE','DF','ES','GO','MA','MT','MS',
           'MG','PA','PB','PR','PE','PI','RJ','RN','RS','RO','RR','SC','SP','SE','TO'}
    estados_encontrados = re.findall(r'Estado[.\s]+:\s*([A-Z]{2})', texto, re.IGNORECASE)
    for uf in estados_encontrados:
        if uf in UFS:
            dados['estado'] = uf  # pega o último (cliente), não o primeiro (empresa RJ)

    # ── Datas ─────────────────────────────────────────────────────────────────
    # Data emissão: próxima de "EMISSÃO" no texto
    m = re.search(r'EMISS[ÃA]O[^0-9]*(\d{1,2}/\d{2}/\d{4})', texto, re.IGNORECASE)
    if m:
        d = m.group(1)
        if len(d.split('/')[0]) == 1:
            d = '0' + d
        dados['data_emissao'] = d

    # Data entrega: a mais frequente no texto (aparece em cada item)
    todas_datas = re.findall(r'\b(\d{2}/\d{2}/\d{4})\b', texto)
    if todas_datas:
        contagem = Counter(todas_datas)
        dados['data_entrega'] = contagem.most_common(1)[0][0]
        if not dados['data_emissao'] and len(contagem) > 1:
            dados['data_emissao'] = contagem.most_common()[-1][0]

    # ── Frete ─────────────────────────────────────────────────────────────────
    m = re.search(r'Frete CIF ou FOB:(.+?)(?:\n|CPF)', texto, re.IGNORECASE)
    if m:
        dados['frete'] = m.group(1).strip()

    # ── Origem de vendas ──────────────────────────────────────────────────────
    m = re.search(r'Origem de Vendas:([^\n\r]+?)(?:\s+Origem do Cliente|$)', texto, re.IGNORECASE | re.MULTILINE)
    if m:
        dados['origem_vendas'] = m.group(1).strip()

    # ── Observações da NF ─────────────────────────────────────────────────────
    m = re.search(r'Observações NF:\s*(.+?)(?:\n.*?____|$)', texto, re.IGNORECASE | re.DOTALL)
    if m:
        obs = m.group(1).strip().replace('\n', ' ')
        dados['obs_gerais'] = obs[:500]

    # ── Itens ─────────────────────────────────────────────────────────────────
    # Cada linha de item começa com código de transação de 5 dígitos (90155 ou 90175)
    # Formato: TRANS CODIGO [LOT] DESCRICAO DATA QTD VLR_UNIT TABELA VLR_BRUTO
    # Os valores monetários usam formato BR: 1.234,56
    itens = []
    linhas_item = re.findall(r'^\d{5}\s+(.+)', texto, re.MULTILINE)

    for linha in linhas_item:
        linha = linha.strip()
        # Extrai os últimos 4 números em formato BR (X,XX ou X.XXX,XX)
        numeros = re.findall(r'[\d.]+,\d{2}', linha)
        if len(numeros) < 3:
            continue

        # últimos 4 números = qty, vlr_unit, tabela, vlr_bruto
        # (ou 3 se tabela == vlr_unit)
        if len(numeros) >= 4:
            qtd_s, vlr_unit_s = numeros[-4], numeros[-3]
            vlr_bruto_s = numeros[-1]
        else:
            qtd_s, vlr_unit_s = numeros[-3], numeros[-2]
            vlr_bruto_s = numeros[-1]

        try:
            qtd      = num_br(qtd_s)
            vlr_unit = num_br(vlr_unit_s)
            vlr_bruto= num_br(vlr_bruto_s)
        except Exception:
            continue

        if qtd <= 0:
            continue

        # Remove os números do final para obter o prefixo (código + lote + descrição)
        primeiro_num = re.search(r'[\d.]+,\d{2}', linha)
        prefixo = linha[:primeiro_num.start()].strip() if primeiro_num else linha

        # Extrai código do produto e lote opcional
        m_cod = re.match(r'([A-Z]{2,5}\d{4,5})\s+(?:(\d{3})\s+)?(.+)', prefixo)
        if not m_cod:
            continue

        codigo    = m_cod.group(1).strip()
        lote      = (m_cod.group(2) or '').strip()
        descricao = m_cod.group(3).strip()

        # Limpa datas e lixo da descrição
        descricao = re.sub(r'\d{2}/\d{2}/\d{4}', '', descricao)
        descricao = re.sub(r'\s+', ' ', descricao).strip()
        # Remove sufixos numéricos soltos (artefatos de extração)
        descricao = re.sub(r'\s+\d{1,2}$', '', descricao).strip()

        if not descricao or len(descricao) < 3:
            continue

        itens.append({
            'codigo':      codigo,
            'derivacao':   lote,
            'descricao':   descricao,
            'quantidade':  qtd,
            'vlr_unitario': vlr_unit,
            'vlr_bruto':   vlr_bruto,
        })

    dados['itens'] = itens
    return dados


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
