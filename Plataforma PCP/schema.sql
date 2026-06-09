-- ============================================================
-- Schema — Confiance Medical PCP
-- Execute este script no seu MySQL para criar o banco de dados
-- ============================================================

CREATE DATABASE IF NOT EXISTS confiance_pcp
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE confiance_pcp;

-- Tabela de pedidos
CREATE TABLE IF NOT EXISTS pedidos (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    numero_pedido   VARCHAR(50)  NOT NULL UNIQUE,
    cliente         VARCHAR(255),
    estado          VARCHAR(10),
    data_emissao    VARCHAR(20),
    data_entrega    VARCHAR(20),
    origem_vendas   VARCHAR(100),
    frete           VARCHAR(100),
    obs_gerais      TEXT,
    segmento        VARCHAR(100),
    valor_total     DECIMAL(15,2) DEFAULT 0,
    total_volumes   INT DEFAULT 0,

    -- Previsões
    prev_liberacao  VARCHAR(20),
    prev_faturamento VARCHAR(30),
    prev_expedicao  VARCHAR(30),

    -- Fluxo de liberação
    liberado        TINYINT(1) DEFAULT 0,
    status_liberado VARCHAR(60),
    data_liberacao  VARCHAR(20),
    data_faturamento VARCHAR(20),
    num_nf          VARCHAR(50),
    transportadora  VARCHAR(100),
    data_entrega_real VARCHAR(20),
    nf_pdf          LONGBLOB,
    nf_pdf_nome     VARCHAR(255),

    -- Finalização
    finalizado      TINYINT(1) DEFAULT 0,
    finalizado_em   VARCHAR(30),

    criado_em       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- Tabela de itens do pedido
CREATE TABLE IF NOT EXISTS itens_pedido (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    pedido_id   INT NOT NULL,
    codigo      VARCHAR(100),
    derivacao   VARCHAR(50),
    descricao   TEXT,
    quantidade  DECIMAL(10,2) DEFAULT 0,
    vlr_unitario DECIMAL(15,2) DEFAULT 0,
    vlr_bruto   DECIMAL(15,2) DEFAULT 0,
    data_entrega VARCHAR(20),
    status      VARCHAR(50) DEFAULT 'Não iniciado',
    observacao  TEXT,
    FOREIGN KEY (pedido_id) REFERENCES pedidos(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Índices úteis
CREATE INDEX idx_pedidos_liberado   ON pedidos(liberado, finalizado);
CREATE INDEX idx_pedidos_entrega    ON pedidos(data_entrega);
CREATE INDEX idx_itens_pedido_id    ON itens_pedido(pedido_id);
CREATE INDEX idx_itens_status       ON itens_pedido(status);
