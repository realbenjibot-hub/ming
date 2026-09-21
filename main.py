"""CLI: python main.py research|refresh|execute|review|report|status|kill|resume|morning"""
import sys, json
from bot.services import Services
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    svc = Services()
    if cmd == "status": print(json.dumps(svc.state(), indent=1, default=str)); sys.exit()
    print(json.dumps(svc.run(cmd), indent=1, default=str))
