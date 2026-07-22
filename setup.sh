#!/bin/bash
# CareFlow — One-time setup script
# Run: bash setup.sh

echo "=== CareFlow Setup ==="

# 1. Install Python dependencies
echo "Installing Python packages..."
pip3 install -r requirements.txt

# 2. Generate data + run scoring + train model
echo "Generating synthetic data..."
python3 data/generator.py

echo "Running health scoring..."
python3 -c "
import sys; sys.path.insert(0, '.')
from scoring.health_score import run_scoring
run_scoring()
print('Health scoring done')
"

echo "Training churn model..."
python3 -c "
import sys; sys.path.insert(0, '.')
from ml.shap_explainer import explain_all
from ml.churn_model import train_model, predict_all, save_predictions
train_model()
preds = predict_all()
explanations = explain_all()
save_predictions(preds, explanations)
print('Churn model done')
"

# 3. Start n8n via Docker
echo "Starting n8n..."
docker run -d \
  --name careflow-n8n \
  -p 5678:5678 \
  -v careflow_n8n_data:/home/node/.n8n \
  -e N8N_BASIC_AUTH_ACTIVE=true \
  -e N8N_BASIC_AUTH_USER=admin \
  -e N8N_BASIC_AUTH_PASSWORD=careflow123 \
  n8nio/n8n 2>/dev/null || echo "n8n already running"

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Start the API:       uvicorn api.main:app --reload --port 8000"
echo "Start dashboard:     streamlit run dashboard/streamlit_app.py"
echo "Open n8n:            http://localhost:5678  (admin / careflow123)"
echo "API docs:            http://localhost:8000/docs"
