"""
并发压力测试：模拟多模块同时访问分库。
测试场景：
  1. 股票因子写入 + 期货因子写入 同时进行
  2. 股票因子读取 + 期货因子写入 同时进行
  3. 双市场同时读取

验证目标：分库后股票/期货两库物理隔离，跨市场并发读写零锁冲突。
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fts.factor_engine.factor_db import FactorRepository

# 线程异常收集器（任一写线程异常 → 判定失败）
_thread_errors: list[Exception] = []
_errors_lock = threading.Lock()


def _record_error(exc: Exception) -> None:
    with _errors_lock:
        _thread_errors.append(exc)


def _cleanup(repo, prefix: str = "test_concurrent_") -> None:
    """清理测试残留（写线程必须清理，含异常路径）。"""
    try:
        con = repo._get_conn()
        con.execute(f"DELETE FROM factor_catalog WHERE factor_id LIKE '{prefix}%'")
    except Exception as e:  # noqa: BLE001
        print(f"  [Cleanup] 清理失败: {e}")


def read_stock():
    repo = FactorRepository(market="stock")
    for i in range(20):
        repo._execute("SELECT factor_id, name FROM factor_catalog LIMIT 5").fetchall()
        _ = repo._execute("SELECT count(*) FROM factor_catalog").fetchone()[0]
        time.sleep(0.01)
    print("  [Stock Reader] 完成 20 次读取")


def read_futures():
    repo = FactorRepository(market="futures")
    for i in range(20):
        repo._execute("SELECT factor_id, name FROM factor_catalog LIMIT 5").fetchall()
        _ = repo._execute("SELECT count(*) FROM factor_catalog").fetchone()[0]
        time.sleep(0.01)
    print("  [Futures Reader] 完成 20 次读取")


def write_stock():
    repo = FactorRepository(market="stock")
    con = repo._get_conn()
    try:
        for i in range(10):
            con.execute(
                "INSERT INTO factor_catalog (factor_id, name, code, code_hash, market) VALUES (?, ?, ?, ?, 'stock')",
                [f"test_concurrent_{i}", f"test_factor_{i}", "concurrent_test_code", f"ch_{i}"],
            )
            time.sleep(0.02)
        print("  [Stock Writer] 完成 10 次写入")
    except Exception as e:  # noqa: BLE001
        _record_error(e)
        print(f"  [Stock Writer] 写入失败: {e}")
    finally:
        _cleanup(repo)


def write_futures():
    repo = FactorRepository(market="futures")
    con = repo._get_conn()
    try:
        for i in range(10):
            con.execute(
                "INSERT INTO factor_catalog (factor_id, name, code, code_hash, market) VALUES (?, ?, ?, ?, 'futures')",
                [f"test_concurrent_{i}", f"test_factor_{i}", "concurrent_test_code", f"ch_{i}"],
            )
            time.sleep(0.02)
        print("  [Futures Writer] 完成 10 次写入")
    except Exception as e:  # noqa: BLE001
        _record_error(e)
        print(f"  [Futures Writer] 写入失败: {e}")
    finally:
        _cleanup(repo)


def run_scenario(name: str, targets: list[tuple[object, tuple]]) -> bool:
    """运行场景并汇总线程异常。"""
    print(f"场景: {name}")
    with _errors_lock:
        _thread_errors.clear()
    threads = [threading.Thread(target=t, args=a) for t, a in targets]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    with _errors_lock:
        failed = len(_thread_errors)
    if failed:
        print(f"  ✗ {name} 失败: {failed} 个线程异常")
        return False
    print(f"  ✓ {name} 通过")
    return True


def main() -> int:
    print("=== 并发压力测试（分库隔离验证） ===")
    print()
    ok = True
    # 场景 1: 双市场同时读取
    ok &= run_scenario("双市场同时读取", [(read_stock, ()), (read_futures, ())])
    # 场景 2: 双市场同时写入（物理隔离 → 无锁冲突）
    ok &= run_scenario("双市场同时写入", [(write_stock, ()), (write_futures, ())])
    # 场景 3: 混合读写（每市场单写线程，避免同库写竞争混淆分库隔离验证）
    ok &= run_scenario(
        "混合读写（双市场）",
        [
            (read_stock, ()),
            (read_stock, ()),
            (read_futures, ()),
            (read_futures, ()),
            (write_stock, ()),
            (write_futures, ()),
        ],
    )
    print()
    if ok:
        print("=== 并发压力测试全部通过 ===")
        return 0
    print("=== 并发压力测试存在失败（见上方 ✗ 与线程异常） ===")
    return 1


if __name__ == "__main__":
    sys.exit(main())
