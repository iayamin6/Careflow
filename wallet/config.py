import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.getenv("CAREFLOW_WALLET_DB", ROOT / "data/wallet.db"))
MODEL_DIR = Path(os.getenv("CAREFLOW_MODEL_DIR", ROOT / "ml/wallet_models"))
REDIS_URL = os.getenv("REDIS_URL", "")
MLFLOW_URI = os.getenv(
    "MLFLOW_TRACKING_URI", "sqlite:///" + str(ROOT / "data/mlflow.db")
)
