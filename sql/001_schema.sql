-- Schéma de la base "fuel" (Étape 4)

-- Chaque ZIP téléchargé. Le sha256 unique évite de retraiter deux fois le même fichier.
CREATE TABLE source_file (
    source_file_id  BIGSERIAL    PRIMARY KEY,
    file_name       TEXT         NOT NULL,
    url             TEXT         NOT NULL,
    sha256          CHAR(64)     NOT NULL UNIQUE,
    size_bytes      BIGINT       NOT NULL CHECK (size_bytes > 0),
    downloaded_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Chaque exécution du pipeline (pour suivre la dernière réussie).
CREATE TABLE pipeline_run (
    run_id         BIGSERIAL    PRIMARY KEY,
    started_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    status         TEXT         NOT NULL DEFAULT 'running'
                   CHECK (status IN ('running', 'success', 'failed')),
    rows_read      BIGINT,
    rows_inserted  BIGINT,
    rows_rejected  BIGINT,
    error_message  TEXT
);

-- Une ligne par station (identifiant officiel).
CREATE TABLE station (
    station_id   BIGINT            PRIMARY KEY,
    address      TEXT,
    city         TEXT,
    postal_code  CHAR(5),
    latitude     DOUBLE PRECISION,           -- en degrés (déjà divisée par 100 000)
    longitude    DOUBLE PRECISION,
    road_type    TEXT,                       -- valeur brute : A autoroute, R route...
    on_map       BOOLEAN           NOT NULL DEFAULT false,  -- coordonnées exploitables
    first_seen   DATE,
    last_seen    DATE
);

-- Un prix à une date de mise à jour. La clé primaire empêche les doublons.
CREATE TABLE price_event (
    station_id      BIGINT        NOT NULL REFERENCES station (station_id),
    fuel            TEXT          NOT NULL
                    CHECK (fuel IN ('Gazole', 'SP95', 'SP98', 'E10', 'E85', 'GPLc')),
    updated_at      TIMESTAMP     NOT NULL,   -- heure locale française, sans fuseau
    price_eur_l     NUMERIC(6,3)  NOT NULL CHECK (price_eur_l > 0),
    is_suspect      BOOLEAN       NOT NULL DEFAULT false,   -- prix absurde : gardé mais exclu des stats
    source_file_id  BIGINT        NOT NULL REFERENCES source_file (source_file_id),
    PRIMARY KEY (station_id, fuel, updated_at)
);

-- Pour les questions du type "tous les prix du gazole entre deux dates".
CREATE INDEX idx_price_event_fuel_date ON price_event (fuel, updated_at);

-- Lignes écartées, avec la raison. Tout est en texte car ces valeurs sont justement illisibles.
CREATE TABLE rejected_row (
    rejected_id     BIGSERIAL    PRIMARY KEY,
    source_file_id  BIGINT       REFERENCES source_file (source_file_id),
    station_id      TEXT,
    fuel            TEXT,
    raw_value       TEXT,
    raw_updated_at  TEXT,
    reason          TEXT         NOT NULL,
    rejected_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);