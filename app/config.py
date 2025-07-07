## 환경 변수 설정
from dotenv import load_dotenv
import os


load_dotenv()

# 바이낸스 키
EX_API_KEY = os.getenv("EXCHANGE_API_KEY")
EX_API_SECRET = os.getenv("EXCHANGE_API_SECRET")
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

# ── 거래 파라미터 ────────────────────────────────────
# 잔고의 몇 %를 사용해서 진입할지
BUY_PCT        = float(os.getenv("BUY_PCT", "0.98"))
# 레버리지 배율
TRADE_LEVERAGE = int(os.getenv("TRADE_LEVERAGE", "5"))
# 익절 비율 (+0.5% → 1.005)
TP_RATIO       = float(os.getenv("TP_RATIO", "1.005"))
# 익절 시 분할 매도의 비중 (50% → 0.5)
TP_PART_RATIO  = float(os.getenv("TP_PART_RATIO", "0.5"))
# 손절 비율 (-0.5% → 0.995)
SL_RATIO       = float(os.getenv("SL_RATIO", "0.995"))
# 포지션 체크 주기 (초)
POLL_INTERVAL  = float(os.getenv("POLL_INTERVAL", "1.0"))
# 최대 대기 시간 (초)
MAX_WAIT       = int(os.getenv("MAX_WAIT", "15"))