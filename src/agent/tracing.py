"""Best-effort local JSONL events and session replay (never executes tools)."""
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import re


DEFAULT_TRACE_PATH = Path(__file__).resolve().parents[2] / 'reports' / 'agent_trace.jsonl'


def _safe(value):
    if isinstance(value, dict):
        return {str(k): ('[REDACTED]' if re.search(r'api.?key|authorization|secret|password|reasoning_content|chain_of_thought|thinking', str(k), re.I)
                         else _safe(v)) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_safe(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r'sk-[A-Za-z0-9_-]{8,}', '[REDACTED]', value)
        return re.sub(r'(?i)(bearer\s+|(?:api[_ -]?key|authorization)\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+', r'\1[REDACTED]', value)
    return value


class JsonlTracer:
    def __init__(self, path=DEFAULT_TRACE_PATH):
        self.path = Path(path)

    def record(self, session_id, step, event, **details):
        try:
            row = dict(session_id=session_id, step=step, event=event,
                       timestamp=datetime.now(timezone.utc).isoformat(), **details)
            encoded = json.dumps(_safe(row), ensure_ascii=False, allow_nan=False)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open('a', encoding='utf-8') as handle:
                handle.write(encoded + '\n')
            return True
        except Exception:
            return False

    def replay(self, session_id):
        """Return ordered saved events; tolerate a torn or malformed line."""
        events = []
        try:
            with self.path.open(encoding='utf-8') as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                        if isinstance(row, dict) and row.get('session_id') == session_id:
                            events.append(row)
                    except (ValueError, TypeError):
                        continue
        except OSError:
            pass
        return events


def main():
    parser = argparse.ArgumentParser(description='Replay a saved Agent session without executing it')
    parser.add_argument('session_id')
    parser.add_argument('--path', default=str(DEFAULT_TRACE_PATH))
    args = parser.parse_args()
    print(json.dumps(JsonlTracer(args.path).replay(args.session_id), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
