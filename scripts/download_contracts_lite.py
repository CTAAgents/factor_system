"""Download all futures contracts using lightweight HTTP (no pandas/akshare).
Uses requests+json directly to avoid MemoryError on low-memory systems."""
import time, os, json, requests, duckdb
from datetime import datetime

FUTURES = [
    ("RB",2019),("HC",2019),("I",2019),("J",2019),("JM",2019),("SF",2019),("SM",2019),
    ("SC",2019),("LU",2020),("FU",2019),("BU",2019),("PG",2020),("PX",2023),("TA",2019),
    ("PF",2020),("EG",2019),("EB",2020),("V",2019),("PP",2019),("L",2019),("MA",2019),
    ("UR",2020),("SA",2020),("BR",2023),
    ("C",2019),("CS",2019),("M",2019),("RM",2019),("Y",2019),("P",2019),("OI",2019),
    ("A",2019),("B",2019),("FB",2019),("BB",2019),("JD",2019),("LH",2021),("AP",2019),
    ("CJ",2020),("SR",2019),("CF",2019),("CY",2020),("PK",2021),("RR",2020),
    ("CU",2019),("AL",2019),("ZN",2019),("PB",2019),("NI",2019),("SN",2019),
    ("AU",2019),("AG",2019),("AO",2023),("SI",2022),("LC",2023),
    ("IF",2019),("IH",2019),("IC",2019),("IM",2022),
]

DB = "D:/Programs/factor_system/data/fts_history.duckdb"
CY = datetime.now().year
URL = "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_V21052021_4_12=/InnerFuturesNewService.getDailyKLine"
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})

def gen_codes(sym, sy):
    return [f"{sym}{str(y)[-2:]}{m:02d}" for y in range(sy, CY+2) for m in range(1,13)]


def fetch(symbol, retries=3):
    params = {"symbol": symbol, "type": "2021_04_12"}
    last = None
    for a in range(retries):
        try:
            r = S.get(URL, params=params, timeout=15)
            if r.status_code != 200:
                last = f"HTTP {r.status_code}"; time.sleep(0.5*(a+1)); continue
            t = r.text
            if "=(" not in t or ");" not in t:
                last = "Invalid JSONP"; time.sleep(0.3); continue
            payload = t.split("=(")[1].split(");")[0]
            data = json.loads(payload)
            return data if data else []
        except requests.exceptions.RequestException as e:
            last = f"Req: {type(e).__name__}"; time.sleep(0.5*(a+1))
        except (json.JSONDecodeError, IndexError, ValueError) as e:
            last = f"Parse: {type(e).__name__}"; time.sleep(0.3)
    raise RuntimeError(last or "unknown")

def main():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    conn = duckdb.connect(DB)
    conn.execute("CREATE TABLE IF NOT EXISTS contract_kline (contract VARCHAR NOT NULL, symbol VARCHAR NOT NULL, period VARCHAR NOT NULL, date VARCHAR NOT NULL, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE, amount DOUBLE, PRIMARY KEY (contract, period, date))")
    conn.commit()
    try:
        existing = {r[0] for r in conn.execute("SELECT DISTINCT contract FROM contract_kline").fetchall()}
    except Exception:
        existing = set()
    conn.close()
    print(f"Already: {len(existing)} contracts")
    all_c = []
    for s, sy in FUTURES:
        all_c.extend([(c, s) for c in gen_codes(s, sy)])
    pending = [(c, s) for c, s in all_c if c not in existing]
    print(f"Total: {len(all_c)}, Pending: {len(pending)}")
    print()
    suc = fail = nod = tot = 0
    errs = []
    buf = []
    t0 = time.time()
    conn = duckdb.connect(DB)
    BATCH = 1200
    for i, (con, sym) in enumerate(pending, 1):
        if i % 50 == 0:
            el = time.time() - t0
            r = i / el if el > 0 else 0
            eta = (len(pending) - i) / r / 60 if r > 0 else 0
            print(f"  {i}/{len(pending)} ({i/len(pending)*100:.1f}%) s={suc} n={nod} f={fail} {r:.1f}/s ETA={eta:.0f}m", flush=True)
        try:
            data = fetch(con)
            if not data:
                nod += 1; continue
            for row in data:
                d = row.get("d", "")
                if not d: continue
                o = float(row.get("o", 0) or 0)
                h = float(row.get("h", 0) or 0)
                l = float(row.get("l", 0) or 0)
                c = float(row.get("c", 0) or 0)
                v = float(row.get("v", 0) or 0)
                buf.append((con, sym, "daily", d, o, h, l, c, v, c*v))
            suc += 1
            tot += len(data)
            if len(buf) >= BATCH:
                conn.executemany("INSERT OR REPLACE INTO contract_kline (contract, symbol, period, date, open, high, low, close, volume, amount) VALUES (?,?,?,?,?,?,?,?,?,?)", buf)
                conn.commit()
                buf.clear()
            time.sleep(0.05)
        except Exception as e:
            fail += 1
            msg = str(e)
            if "Length mismatch" not in msg:
                errs.append(f"{con}: {msg}")
            if fail % 50 == 0 and fail > 0:
                time.sleep(2.0)
    if buf:
        conn.executemany("INSERT OR REPLACE INTO contract_kline (contract, symbol, period, date, open, high, low, close, volume, amount) VALUES (?,?,?,?,?,?,?,?,?,?)", buf)
        conn.commit()
    conn.close()
    el = time.time() - t0
    print()
    print("=" * 60)
    print(f"Done in {el/60:.1f} min: success={suc} no_data={nod} fail={fail}")
    print(f"Total bars: {tot:,}")
    print(f"DB size: {os.path.getsize(DB)/1024/1024:.2f} MB")
    if errs:
        print(f"\nErrors ({len(errs)}):")
        for e in errs[:30]:
            print(f"  {e}")
    print("\n=== Verification ===")
    conn = duckdb.connect(DB, read_only=True)
    r = conn.execute("SELECT COUNT(*), COUNT(DISTINCT contract), COUNT(DISTINCT symbol) FROM contract_kline").fetchone()
    print(f"Total: {r[2]} symbols, {r[1]} contracts, {r[0]:,} bars")
    r = conn.execute("SELECT symbol, COUNT(DISTINCT contract) as cnt, COUNT(*) as bars FROM contract_kline GROUP BY symbol ORDER BY cnt DESC LIMIT 10").fetchall()
    print(f"\nTop 10 by contract count:")
    print(f"{'Symbol':<8} {'Contracts':>10} {'Bars':>10}")
    for row in r:
        print(f"{row[0]:<8} {row[1]:>10} {row[2]:>10}")
    conn.close()

if __name__ == "__main__":
    main()
