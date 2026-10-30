"""Generate the database and HTTP reference from an isolated empty application."""
import json
from pathlib import Path
import tempfile
from backend.app import create_app
from backend.store import connect,SCHEMA_VERSION

ROOT=Path(__file__).resolve().parents[1]
DOCUMENT=ROOT/'docs/当前系统完整技术文档.md'


def reference():
    with tempfile.TemporaryDirectory() as directory:
        app=create_app(Path(directory),'documentation-only')
        with connect(app.state.db_path) as db:
            objects=db.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name").fetchall()
            lines=['<a id="s14"></a>','## 14. 数据库字典（自动生成）',f'当前版本：{SCHEMA_VERSION}。以下 DDL 包含表字段、默认值、约束、外键、索引和触发器；不包含任何业务数据。']
            for row in objects:lines += [f"### `{row['name']}`（{row['type']}）",'```sql',row['sql']+';','```']
        schema=app.openapi()
        lines+=['<a id="s15"></a>','## 15. HTTP 接口与请求契约（自动生成）','接口受本机 Host/Origin 检查；除一次性登录码兑换外，API 需要当前会话 Bearer。未声明 response_model 的成功响应须结合业务章节及处理函数理解。','| 方法 | 路径 | 处理函数 |','| --- | --- | --- |']
        for path,methods in sorted(schema['paths'].items()):
            for method,operation in sorted(methods.items()):
                lines.append(f"| {method.upper()} | `{path}` | `{operation.get('operationId','')}` |")
        lines+=['### 参数、请求体和声明响应','```json',json.dumps(schema,ensure_ascii=False,sort_keys=True,indent=2),'```']
        return '\n\n'.join(lines)+'\n\n'


def update(check=False):
    text=DOCUMENT.read_text(encoding='utf-8');start=text.index('<a id="s14"></a>');end=text.index('<a id="s16"></a>')
    generated=reference()
    if check:
        assert text[start:end]==generated,'数据库或接口文档已过期，请运行 python -m tools.generate_reference'
    else:DOCUMENT.write_text(text[:start]+generated+text[end:],encoding='utf-8')


if __name__=='__main__':
    import sys
    update('--check' in sys.argv)
