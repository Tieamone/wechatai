"""Load only the reviewed read-only database module, without GUI package imports."""
import importlib.util
import logging
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]


def load_reader():
    if 'wechatauto.db' in sys.modules:
        return sys.modules['wechatauto.db']
    folder = ROOT/'tools/wechatauto-replica/wechatauto'
    package = types.ModuleType('wechatauto')
    package.__path__ = [str(folder)]
    sys.modules['wechatauto'] = package
    logger = types.ModuleType('wechatauto.logger')
    logger.wxlog = logging.getLogger('wechat-source')
    logger.wxlog.addHandler(logging.NullHandler())
    logger.wxlog.propagate = False
    sys.modules['wechatauto.logger'] = logger
    spec = importlib.util.spec_from_file_location('wechatauto.db', folder/'db.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


if __name__ == '__main__':
    import json
    module = load_reader()
    root = module.auto_detect_db_dir()
    accounts = module._find_account_dirs(root) if root else []
    print(json.dumps({'root':root, 'accounts':[
        {'directory':str(p), 'message_shards':len(list(Path(p).glob('db_storage/message/message_[0-9]*.db')))}
        for p in accounts]}, ensure_ascii=False))
