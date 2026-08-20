"""Write the demo's data/orders.parquet with DuckDB itself.

The demo GIF's whole point is that the extension needs only `duckdb` — no
pandas, no pyarrow — so the fixture is written by DuckDB too. Two columns carry
NULLs on purpose, because "which rows are missing a churn score" is the question
the recorded session asks.
"""

import duckdb

duckdb.sql("""
COPY (
  SELECT
    1000 + i                                                AS order_id,
    ['seoul','busan','daegu','incheon','gwangju'][(i % 5) + 1]  AS region,
    round(5.0 + ((i * 37) % 47500) / 100.0, 2)              AS amount,
    (i % 5) + 1                                             AS rating,
    CASE WHEN i % 8 = 0 THEN NULL
         ELSE round(((i * 17) % 1000) / 1000.0, 3) END      AS churn_score,
    CASE WHEN i % 27 = 0 THEN NULL
         ELSE ['web','ios','android'][(i % 3) + 1] END      AS channel
  FROM range(1, 2001) t(i)
) TO 'data/orders.parquet' (FORMAT parquet)
""")

print(duckdb.sql("""
    SELECT count(*) AS n,
           count(churn_score) AS with_churn,
           count(channel)     AS with_channel
    FROM 'data/orders.parquet'
""").fetchall())
