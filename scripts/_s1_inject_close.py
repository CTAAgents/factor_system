from __future__ import annotations
import io
from pathlib import Path

DECORATOR_DEF = '''def _release_repo_after(func):
    """E.4 S1: release L3 repo write lock after method exits (decorator)."""
    from functools import wraps

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        finally:
            if getattr(self, "_repo", None) is not None:
                try:
                    self._repo.close()
                except Exception:
                    pass
                self._repo = None

    return wrapper


'''

ANCHOR_GETREPO = '    def _get_repo(self):'
ANCHOR_PROMOTE = '    def _promote_to_elite('


def inject(p):
    s = io.open(p, encoding='utf-8').read()
    if '@_release_repo_after' in s:
        print('SKIP', Path(p).name, 'already injected')
        return
    assert s.count(ANCHOR_GETREPO) == 1, (Path(p).name, 'getrepo', s.count(ANCHOR_GETREPO))
    assert s.count(ANCHOR_PROMOTE) == 1, (Path(p).name, 'promote', s.count(ANCHOR_PROMOTE))
    s = s.replace(ANCHOR_GETREPO, DECORATOR_DEF + ANCHOR_GETREPO)
    s = s.replace(ANCHOR_PROMOTE, '    @_release_repo_after\n' + ANCHOR_PROMOTE)
    io.open(p, 'w', encoding='utf-8').write(s)
    print('OK', Path(p).name)


inject(r'd:\Programs\factor_system\fts\factor_engine\evolution_loop.py')
inject(r'd:\Programs\factor_system\fts\factor_engine\evolution_futures.py')
