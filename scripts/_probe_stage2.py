"""临时探测：复查 SHFE/DCE 仓单接口可用性。用完即删。"""
import threading
import akshare as ak


def timed(name: str, fn, timeout: int = 30):
    res = {}

    def _r():
        try:
            res["v"] = fn()
        except Exception as e:  # noqa: BLE001
            res["e"] = f"{type(e).__name__}: {e}"

    t = threading.Thread(target=_r)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return f"{name}: TIMEOUT>{timeout}s"
    if "e" in res:
        return f"{name}: FAIL {res['e']}"
    v = res["v"]
    if isinstance(v, dict):
        keys = list(v.keys())
        first = v[keys[0]] if keys else None
        return f"{name}: OK dict keys={keys[:8]} rows={len(first) if first is not None else 0} cols={list(first.columns)[:6] if first is not None else []}"
    return f"{name}: OK shape={getattr(v, 'shape', None)} cols={list(getattr(v, 'columns', []))[:6]}"


if __name__ == "__main__":
    print(timed("SHFE 20260807", lambda: ak.futures_shfe_warehouse_receipt("20260807")))
    print(timed("DCE 20260807", lambda: ak.futures_warehouse_receipt_dce("20260807")))
