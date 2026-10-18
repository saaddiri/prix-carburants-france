"""Où sont les prix "bizarres" ? Compare chaque prix à la médiane nationale du jour (même carburant).

ratio = prix de la station / médiane du jour. Un ratio de 1,00 = prix médian ; 0,70 = 30 % moins cher.
Lit data/analytics/price_daily.parquet et daily_stats.parquet (créés par `python -m fuel.analytics`).
Ne modifie rien.   Usage : python explore\\ratio_tails.py
"""
import duckdb

con = duckdb.connect()
con.execute("""CREATE VIEW r AS
    SELECT p.fuel, p.station_id, p.day, p.price::DOUBLE AS price,
           p.price::DOUBLE / s.median_price AS ratio
    FROM 'data/analytics/price_daily.parquet' p
    JOIN 'data/analytics/daily_stats.parquet' s USING (day, fuel)""")


def show(title, sql):
    cur = con.execute(sql)
    cols = [c[0] for c in cur.description]
    print(f"\n{title}")
    print("  " + " | ".join(f"{c:>9}" for c in cols))
    for row in cur.fetchall():
        print("  " + " | ".join(f"{str(v):>9}" for v in row))


show("1) Combien de lignes (station x jour) selon l'écart à la médiane du jour",
     """SELECT fuel, count(*) AS lignes,
               count(*) FILTER (WHERE ratio < 0.6)                   AS "<0.6",
               count(*) FILTER (WHERE ratio >= 0.6 AND ratio < 0.7)  AS "0.6-0.7",
               count(*) FILTER (WHERE ratio >= 0.7 AND ratio < 0.8)  AS "0.7-0.8",
               count(*) FILTER (WHERE ratio > 1.2 AND ratio <= 1.3)  AS "1.2-1.3",
               count(*) FILTER (WHERE ratio > 1.3 AND ratio <= 1.5)  AS "1.3-1.5",
               count(*) FILTER (WHERE ratio > 1.5)                   AS ">1.5"
        FROM r GROUP BY fuel ORDER BY fuel""")

show("2) Les 15 stations avec le plus de jours à ratio < 0.7 ou > 1.3 (durable = peut-être réel ; court = erreur ?)",
     """SELECT fuel, station_id, count(*) AS jours, min(day) AS du, max(day) AS au,
               round(min(price), 3) AS prix_min, round(max(price), 3) AS prix_max
        FROM r WHERE ratio < 0.7 OR ratio > 1.3
        GROUP BY fuel, station_id ORDER BY jours DESC LIMIT 15""")