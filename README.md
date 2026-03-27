# 🤖 Trading Bot — Kotak Breakout Strategy

An algorithmic trading bot with a **FastAPI backend** and **React frontend dashboard**, built for real-time options trading using the Kotak Neo API.

---

## 📁 Project Structure

```
Trading_Bot_Kotak_Breakout/
├── backend/        # FastAPI server, broker integration, strategy engine
├── frontend/       # React dashboard (Vite + MUI)
├── START_EVERYTHING.bat
└── STOP_EVERYTHING.bat
```

---

## ✨ Features

- 🔐 Auto-login with Kotak Neo broker API
- 📡 Real-time market data via WebSockets
- 📊 Multi-strategy engine (RSI, MA Crossover, Candlestick Patterns, UOA Scanner)
- 🛡️ Risk management & trailing stop-loss
- 📈 Live React dashboard with candlestick charts, option chain, P&L tracking
- 🔔 Audio & visual trade alerts
- 🗃️ PostgreSQL trade logging & analytics

---

## 🛠️ Tech Stack

| Layer    | Tech                                      |
|----------|-------------------------------------------|
| Backend  | Python, FastAPI, Uvicorn, Kotak Neo API   |
| Frontend | React 18, Vite, Material-UI, TradingView  |
| Database | PostgreSQL                                |
| Broker   | Kotak Neo API                             |

---

## ⚙️ Setup

### Backend

```bash
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

Create a `.env` file in `backend/`:
```env
DB_HOST=your_db_host
DB_PORT=5432
DB_USER=your_db_user
DB_PASSWORD=your_db_password
DB_NAME_TODAY=trading_kotak_today
DB_NAME_ALL=trading_kotak_all
API_KEY=your_api_key
```

Run:
```bash
uvicorn main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

---

## 🚀 Quick Start (Windows)

Just double-click:
```
START_EVERYTHING.bat   ← starts backend + frontend
STOP_EVERYTHING.bat    ← stops everything
```

---

## ⚠️ Important

- Never commit `.env`, `broker_config.json`, `strategy_params.json`, or `*.db` files
- PostgreSQL credentials must never be hardcoded — always use `.env`
- All sensitive files are listed in `.gitignore`

---

## 📄 License

For personal/educational use only. Not financial advice.
