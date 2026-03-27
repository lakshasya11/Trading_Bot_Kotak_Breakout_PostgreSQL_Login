import asyncio
import json
import pandas as pd
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from datetime import datetime
import os
import time
from collections import defaultdict
from fastapi.responses import RedirectResponse, FileResponse
import socket
import logging
import sys

# ===== WINDOWS UTF-8 ENCODING FIX =====
# Configure UTF-8 encoding for console output to handle emojis
if sys.platform == 'win32':
    try:
        import codecs
        if hasattr(sys.stdout, 'buffer'):
            sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'replace')
            sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'replace')
        else:
            # Already wrapped or redirected (e.g., with Tee-Object)
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception as e:
        # Fallback: continue without UTF-8 reconfiguration
        print(f"[WARN] Could not configure UTF-8 encoding: {e}")

# ===== LOGGING SETUP =====
# Configure logging to display debug logs in console AND save to file
logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] %(levelname)s - %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),  # Console output
        logging.FileHandler('bot_debug.log', mode='a', encoding='utf-8')  # File output with UTF-8
    ]
)
logger = logging.getLogger(__name__)
logger.info("="*80)
logger.info("BOT STARTING - Debug logging enabled (console + bot_debug.log file)")
logger.info("="*80)

# ===== WINDOWS SOCKET COMPATIBILITY FIX =====
# Enable SO_REUSEADDR globally to handle lingering TIME_WAIT connections on Windows
def _socket_init_wrapper(original_socket_init):
    def new_init(self, *args, **kwargs):
        original_socket_init(self, *args, **kwargs)
        # Enable address reuse for Windows compatibility
        try:
            self.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except (OSError, AttributeError):
            pass
    return new_init

socket.socket.__init__ = _socket_init_wrapper(socket.socket.__init__)

from core.broker_factory import broker as kite, generate_session_and_set_token, access_token, BROKER_NAME
from core.websocket_manager import manager
from core.strategy import MARKET_STANDARD_PARAMS
from core.optimiser import OptimizerBot
from core.trade_logger import TradeLogger
from core.bot_service import TradingBotService, get_bot_service
from core.database import today_engine, all_engine, sql_text

# ===== COOLDOWN MECHANISM =====
last_request_times = defaultdict(float)


def _send_bot_stop_email(reason: str = "Bot Stopped"):
    """Send bot stopped email notification via login server. Non-blocking, never raises."""
    try:
        import requests as _req
        with open("broker_config.json", "r") as f:
            cfg = json.load(f)
        user_email = cfg.get("kotak_email", "")
        if user_email:
            _req.post(
                "http://localhost:5001/api/send-bot-alert",
                json={
                    "email": user_email,
                    "name": cfg.get("kotak_user_name", "Trader"),
                    "client_id": cfg.get("kotak_ucc", ""),
                    "event": "stopped",
                    "reason": reason
                },
                timeout=5
            )
    except Exception as e:
        print(f"Bot stop email error: {e}")


def _get_active_user_info() -> dict:
    """Get active user info from broker config (Kotak) or user_profiles.json (Kite)."""
    if BROKER_NAME == "kotak":
        try:
            with open("broker_config.json", "r") as f:
                cfg = json.load(f)
            return {
                "id": cfg.get("kotak_ucc", "kotak"),
                "name": cfg.get("kotak_user_name", "Kotak User"),
                "description": "Kotak Neo Trading Account",
            }
        except Exception:
            return {"id": "kotak", "name": "Kotak User", "description": ""}
    else:
        try:
            with open("user_profiles.json", "r") as f:
                data = json.load(f)
            active_id = data.get("active_user", "")
            user = next(
                (u for u in data.get("users", []) if u["id"] == active_id),
                None,
            )
            if user:
                return {
                    "id": user["id"],
                    "name": user["name"],
                    "description": user.get("description", ""),
                }
        except Exception:
            pass
        return {"id": "unknown", "name": "User", "description": ""}


def cooldown_check(endpoint: str, cooldown_seconds: float = 1.0):
    """
    Prevent rapid-fire requests to same endpoint.
    Protects against button spam and accidental double-clicks.
    """
    now = time.time()
    last_time = last_request_times[endpoint]
    
    if now - last_time < cooldown_seconds:
        remaining = cooldown_seconds - (now - last_time)
        raise HTTPException(
            status_code=429,
            detail=f"Please wait {remaining:.1f} seconds before retrying"
        )
    
    last_request_times[endpoint] = now

# ===== AUTHENTICATION DEPENDENCY =====
def require_auth():
    """Dependency to ensure user is authenticated before trading operations"""
    from core.broker_factory import access_token  # Import at runtime to get current value
    if not access_token:
        raise HTTPException(
            status_code=401, 
            detail="Authentication required. Please authenticate at /api/authenticate first."
        )
    return True

async def _daily_summary_scheduler():
    """Background task: sends daily summary email at 3:31 PM every trading day."""
    while True:
        now = datetime.now()
        target = now.replace(hour=15, minute=31, second=0, microsecond=0)
        if now >= target:
            target = target.replace(day=target.day + 1)
        await asyncio.sleep((target - now).total_seconds())

        try:
            import json as _json
            import pandas as pd
            from dotenv import load_dotenv
            from core.email_notifier import EmailNotifier

            # Ensure login/server/.env is loaded for NOTIFICATION_EMAIL & SMTP settings
            _env_path = os.path.join(os.path.dirname(__file__), '..', 'login', 'server', '.env')
            load_dotenv(os.path.normpath(_env_path), override=True)

            with open("broker_config.json", "r") as f:
                cfg = _json.load(f)

            client_id = cfg.get("kotak_ucc", "")
            name = cfg.get("kotak_user_name", "Trader")
            mode = "LIVE"

            # Use kotak_email as fallback if NOTIFICATION_EMAIL not set
            if not os.getenv("NOTIFICATION_EMAIL") and cfg.get("kotak_email"):
                os.environ["NOTIFICATION_EMAIL"] = cfg["kotak_email"]

            # Read today's trades
            from core.database import today_engine
            with today_engine.connect() as conn:
                df = pd.read_sql_query("SELECT * FROM trades", conn)

            trades_list = df.to_dict("records") if not df.empty else []
            total_trades = len(trades_list)
            net_pnl = float(df["net_pnl"].sum()) if not df.empty and "net_pnl" in df.columns else 0.0
            wins = int((df["net_pnl"] > 0).sum()) if not df.empty and "net_pnl" in df.columns else 0
            losses = total_trades - wins

            # Detect mode from trades
            if not df.empty and "trading_mode" in df.columns:
                mode = "LIVE" if "Live" in str(df["trading_mode"].iloc[0]) else "PAPER"

            EmailNotifier.send_daily_summary(
                client_id=client_id,
                name=name,
                kite_id=client_id,
                mode=mode,
                total_trades=total_trades,
                net_pnl=net_pnl,
                date=datetime.now().strftime("%Y-%m-%d"),
                wins=wins,
                losses=losses,
                trades=trades_list
            )
            print("[DailySummary] Email sent successfully at 3:31 PM")
        except Exception as e:
            print(f"[DailySummary] Failed to send email: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Application startup...")
    TradeLogger.setup_databases()

    # Start daily summary scheduler
    asyncio.create_task(_daily_summary_scheduler())
    print("[OK] Daily summary scheduler started (fires at 3:31 PM)")

    # Reset bot state on startup
    service = await get_bot_service()
    service.is_running = False
    print("[OK] Bot ready (is_running = False)")

    # --- ADDED: Open Position Reconciliation Logic ---
    # No delay needed - WebSocket manager is ready at this point
    from core.broker_factory import access_token as current_token
    if current_token:
        try:
            print("Reconciling open positions...")
            positions = await kite.positions()  # Fixed: kite.positions() is already async
            net_positions = positions.get('net', [])
            open_mis_positions = [
                p['tradingsymbol'] for p in net_positions 
                if p.get('product') == 'MIS' and p.get('quantity') != 0
            ]
            if open_mis_positions:
                warning_message = f"Found open MIS positions at broker: {', '.join(open_mis_positions)}. Manual action may be required."
                print(f"WARNING: {warning_message}")
                # Broadcast a warning to any connected frontend
                await manager.broadcast({
                    "type": "system_warning", 
                    "payload": {
                        "title": "Open Positions Detected on Startup",
                        "message": warning_message
                    }
                })
            else:
                print("[OK] No open MIS positions found")
        except Exception as e:
            print(f"[INFO] Could not reconcile open positions (token may be invalid): {e}")
    else:
        print("[INFO] No access token - skipping position reconciliation")
    # --- END OF ADDED LOGIC ---

    yield
    print("Application shutdown...")
    
    # Stop bot if running
    service = await get_bot_service()
    if service.ticker_manager_instance:
        try:
            await asyncio.wait_for(service.stop_bot(), timeout=15.0)
        except asyncio.TimeoutError:
            print("Bot shutdown timed out")
        except Exception as e:
            print(f"Error during bot shutdown: {e}")
        # Case 4: server stopped (internet/crash/manual kill)
        await asyncio.to_thread(_send_bot_stop_email, "Server Shutdown")
    
    # Mark kite as shutting down
    if hasattr(kite, 'shutdown'):
        await kite.shutdown()
    
    print("Shutdown tasks complete.")

app = FastAPI(lifespan=lifespan)

# Add CORS middleware immediately after app creation
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/db_viewer.html")
async def db_viewer():
    """Serve the DB Viewer HTML page."""
    html_path = os.path.join(os.path.dirname(__file__), "db_viewer.html")
    return FileResponse(html_path, media_type="text/html")


@app.get("/login")
async def login():
    """Redirects to Kite login page."""
    return RedirectResponse(url=kite.login_url())


class TokenRequest(BaseModel): request_token: str
class StartRequest(BaseModel): params: dict; selectedIndex: str
class WatchlistRequest(BaseModel): side: str; strike: int

@app.get("/api/status")
async def get_status():
    # Check if the global access_token variable exists first
    from core.broker_factory import access_token as current_token
    if current_token:
        try:
            # Actively VERIFY the token by making a network API call.
            profile = await kite.profile()  # Fixed: kite.profile() is already async
            # If the call succeeds, we are truly authenticated.
            return {"status": "authenticated", "user": profile.get('user_id')}
        except Exception:
            # If kite.profile() fails, it means the token is invalid.
            # We catch the error and fall through to the unauthenticated response.
            pass
    
    # This is the fallback for BOTH "no token" and "invalid token" cases.
    return {"status": "unauthenticated", "login_url": kite.login_url()}

@app.get("/api/diagnostics")
async def get_diagnostics(auth=Depends(require_auth)):
    """🔍 Diagnostic endpoint to check bot health and instrument status"""
    service = await get_bot_service()
    
    if not service.strategy_instance:
        return {
            "bot_running": False,
            "error": "Bot not started yet"
        }
    
    strategy = service.strategy_instance
    
    diagnostics = {
        "bot_running": service.is_running,
        "instruments_loaded": len(strategy.option_instruments),
        "last_used_expiry": str(strategy.last_used_expiry) if strategy.last_used_expiry else None,
        "initial_subscription_done": strategy.initial_subscription_done,
        "index_symbol": strategy.index_symbol,
        "index_price": strategy.data_manager.prices.get(strategy.index_symbol),
        "token_to_symbol_count": len(strategy.token_to_symbol),
        "lot_size": strategy.lot_size,
        "freeze_limit": strategy.freeze_limit,
        "strike_step": strategy.strike_step,
        "has_position": strategy.position is not None,
        "websocket_connected": service.ticker_manager_instance is not None and 
                              hasattr(service.ticker_manager_instance, 'ws') and 
                              service.ticker_manager_instance.ws is not None,
    }
    
    # Check if we can get strike pairs
    try:
        pairs = strategy.get_strike_pairs(count=3)
        diagnostics["strike_pairs_available"] = len(pairs)
        if pairs:
            diagnostics["sample_strike"] = pairs[0]["strike"]
            diagnostics["sample_ce_symbol"] = pairs[0]["ce"]["tradingsymbol"] if pairs[0]["ce"] else None
            diagnostics["sample_pe_symbol"] = pairs[0]["pe"]["tradingsymbol"] if pairs[0]["pe"] else None
    except Exception as e:
        diagnostics["strike_pairs_error"] = str(e)
    
    return diagnostics

@app.post("/api/authenticate")
async def authenticate(token_request: TokenRequest):
    success, data = generate_session_and_set_token(token_request.request_token)
    if success:
        return {"status": "success", "message": "Authentication successful.", "user": data.get('user_id')}
    raise HTTPException(status_code=400, detail=data)

@app.get("/api/trade_history")
async def get_trade_history():
    def db_call():
        try:
            # 🔥 MULTI-USER FILTERING: Only show trades for the current active user
            user_info = _get_active_user_info()
            active_user_name = user_info.get("name", "Unknown")
            
            with today_engine.connect() as conn:
                query = "SELECT * FROM trades WHERE (user_name = :user OR user_name IS NULL) ORDER BY timestamp ASC"
                df = pd.read_sql_query(sql_text(query), conn, params={"user": active_user_name})
                
                # Manual cleanup to avoid pandas weakref serialization bugs
                import numpy as np
                records = df.to_dict('records')
                for r in records:
                    for k, v in r.items():
                        if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
                            r[k] = None
                return records
        except Exception as e:
            # Return empty list instead of error if table doesn't exist or is empty
            print(f"⚠️ Trade history fetch error: {e}")
            return []
    return await asyncio.to_thread(db_call)

@app.get("/api/trade_history_all")
async def get_all_trade_history():
    def db_call():
        try:
            with all_engine.connect() as conn:
                df = pd.read_sql_query("SELECT * FROM trades ORDER BY timestamp ASC", conn)
                
                # Manual cleanup to avoid pandas weakref serialization bugs
                import numpy as np
                records = df.to_dict('records')
                for r in records:
                    for k, v in r.items():
                        if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
                            r[k] = None
                return records
        except Exception as e:
            # Return empty list instead of error if table doesn't exist or is empty
            print(f"⚠️ All-time trade history fetch error: {e}")
            return []
    return await asyncio.to_thread(db_call)

@app.get("/api/performance")
async def get_performance(service: TradingBotService = Depends(get_bot_service)):
    """Get current daily performance stats (falls back to DB if bot not running)"""
    if service.strategy_instance:
        trades_today = service.strategy_instance.performance_stats["winning_trades"] + service.strategy_instance.performance_stats["losing_trades"]
        return {
            "grossPnl": service.strategy_instance.daily_gross_pnl,
            "totalCharges": service.strategy_instance.total_charges,
            "netPnl": service.strategy_instance.daily_net_pnl,
            "net_pnl": service.strategy_instance.daily_net_pnl,
            "wins": service.strategy_instance.performance_stats["winning_trades"],
            "losses": service.strategy_instance.performance_stats["losing_trades"],
            "trades_today": trades_today
        }
    else:
        # Fallback to Database Summary
        # Fallback to Database Summary
        def get_db_summary():
            try:
                # MULTI-USER FILTERING
                user_info = _get_active_user_info()
                active_user_name = user_info.get("name", "Unknown")
                
                with today_engine.connect() as conn:
                    query = sql_text("""
                        SELECT 
                            SUM(pnl) as grossPnl, 
                            SUM(charges) as totalCharges, 
                            SUM(net_pnl) as netPnl,
                            COUNT(*) as trades_today,
                            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                            SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as losses
                        FROM trades
                        WHERE (user_name = :user OR user_name IS NULL)
                    """)
                    result = conn.execute(query, {"user": active_user_name}).mappings().fetchone()
                    
                    if result and result['trades_today'] > 0:
                        return {
                            "grossPnl": result['grossPnl'] or 0,
                            "totalCharges": result['totalCharges'] or 0,
                            "netPnl": result['netPnl'] or 0,
                            "net_pnl": result['netPnl'] or 0,
                            "wins": result['wins'] or 0,
                            "losses": result['losses'] or 0,
                            "trades_today": result['trades_today'] or 0
                        }
            except Exception as e:
                print(f"Error getting performance summary from DB: {e}")
            return None
            
        summary = await asyncio.to_thread(get_db_summary)
        if summary: return summary
        return {"grossPnl": 0, "totalCharges": 0, "netPnl": 0, "net_pnl": 0, "wins": 0, "losses": 0, "trades_today": 0}

@app.post("/api/optimize")
async def run_optimizer(service: TradingBotService = Depends(get_bot_service)):
    optimizer = OptimizerBot()
    new_params, justifications = await optimizer.find_optimal_parameters()
    if new_params:
        optimizer.update_strategy_file(new_params)
        if service.strategy_instance:
            await service.strategy_instance.reload_params()
            await service.strategy_instance._log_debug("Optimizer", "Live parameter reload successful.")
        return {"status": "success", "report": justifications}
    return {"status": "error", "report": justifications or ["Optimization failed."]}

@app.post("/api/reset_uoa_watchlist")
async def reset_uoa(service: TradingBotService = Depends(get_bot_service)):
    if not service.strategy_instance:
        raise HTTPException(status_code=400, detail="Bot is not running.")
    
    await service.strategy_instance.reset_uoa_watchlist()
    return {"status": "success", "message": "UOA Watchlist has been cleared."}

# --- THIS IS THE CORRECTED FUNCTION ---
@app.post("/api/update_strategy_params")
async def update_strategy_parameters(params: dict, service: TradingBotService = Depends(get_bot_service)):
    try:
        # CRITICAL FIX: Merge with existing params to preserve technical indicators
        try:
            with open("strategy_params.json", "r") as f:
                existing_params = json.load(f)
        except FileNotFoundError:
            existing_params = MARKET_STANDARD_PARAMS.copy()
        
        # Merge: Preserve existing technical params, update only what's sent from UI
        merged_params = {**MARKET_STANDARD_PARAMS, **existing_params, **params}
        
        # Step 1: Update the JSON file with merged parameters
        with open("strategy_params.json", "w") as f:
            json.dump(merged_params, f, indent=4)
        
        # Step 2: If the bot is running, tell it to reload its parameters from the file
        if service.strategy_instance:
            await service.strategy_instance.reload_params()
            await service.strategy_instance._log_debug("System", "Parameters have been updated from UI.")
            
        return {"status": "success", "message": "Parameters updated successfully.", "params": merged_params}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update parameters: {str(e)}")

@app.post("/api/reset_params")
async def reset_parameters(service: TradingBotService = Depends(get_bot_service)):
    try:
        # Step 1: Overwrite the JSON file with the market standard defaults.
        with open("strategy_params.json", "w") as f:
            json.dump(MARKET_STANDARD_PARAMS, f, indent=4)
        
        # Step 2: If the bot is running, tell it to reload its parameters from the file.
        if service.strategy_instance:
            await service.strategy_instance.reload_params()
            await service.strategy_instance._log_debug("System", "Parameters have been reset to market defaults.")
            
        return {"status": "success", "message": "Parameters reset.", "params": MARKET_STANDARD_PARAMS}
    except Exception as e:
        # The str(e) is included for better debugging if something else goes wrong.
        raise HTTPException(status_code=500, detail=f"Failed to reset parameters: {str(e)}")

@app.post("/api/start")
async def start_bot(req: StartRequest, service: TradingBotService = Depends(get_bot_service), authenticated: bool = Depends(require_auth)):
    cooldown_check("start", cooldown_seconds=2.0)
    result = await service.start_bot(req.params, req.selectedIndex)

    # Send bot started email notification
    try:
        import requests as http_req
        import json as _json
        with open("broker_config.json", "r") as f:
            cfg = _json.load(f)
        user_email = cfg.get("kotak_email", "")
        user_name = cfg.get("kotak_user_name", "Trader")
        client_id = cfg.get("kotak_ucc", "")
        if user_email:
            resp = http_req.post(
                "http://localhost:5001/api/send-bot-alert",
                json={
                    "email": user_email,
                    "name": user_name,
                    "client_id": client_id,
                    "event": "started",
                    "index": req.selectedIndex
                },
                timeout=5
            )
            print(f"[Email] Bot start alert sent to {user_email} — status: {resp.status_code}")
        else:
            print("[Email] kotak_email not set in broker_config.json — skipping bot start email")
    except Exception as e:
        print(f"[Email] Bot start email error: {e}")

    return result

@app.post("/api/stop")
async def stop_bot(service: TradingBotService = Depends(get_bot_service), authenticated: bool = Depends(require_auth)):
    cooldown_check("stop", cooldown_seconds=1.0)
    result = await service.stop_bot()
    # Case 2: Stop bot button pressed from dashboard
    await asyncio.to_thread(_send_bot_stop_email, "Stopped by User")
    return result

@app.post("/api/pause")
async def pause_bot(service: TradingBotService = Depends(get_bot_service), authenticated: bool = Depends(require_auth)):
    cooldown_check("pause", cooldown_seconds=1.0)
    return await service.pause_bot()

@app.post("/api/unpause")
async def unpause_bot(service: TradingBotService = Depends(get_bot_service), authenticated: bool = Depends(require_auth)):
    cooldown_check("unpause", cooldown_seconds=1.0)
    return await service.unpause_bot()

@app.post("/api/manual_exit")
async def manual_exit(service: TradingBotService = Depends(get_bot_service), authenticated: bool = Depends(require_auth)):
    cooldown_check("manual_exit", cooldown_seconds=3.0)
    return await service.manual_exit_trade()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, service: TradingBotService = Depends(get_bot_service)):
    conn_start_time = asyncio.get_event_loop().time()
    await manager.connect(websocket)
    print("Client connected. Synchronizing state...")
    
    try:
        # Send active user info immediately
        try:
            active_user_info = _get_active_user_info()
            await websocket.send_json({
                "type": "active_user_update",
                "payload": active_user_info
            })
        except Exception:
            pass

        # Send initial state synchronization
        if service.strategy_instance:
            await service.strategy_instance._update_ui_status()
            # CRITICAL FIX: Always send performance data even if zero (ensures UI shows correct state)
            await service.strategy_instance._update_ui_performance()
            await service.strategy_instance._update_ui_trade_status()
            
            # 🔥 SYNC COMPLETION: Send full trade history list on connect
            try:
                history = await get_trade_history()
                await websocket.send_json({
                    "type": "trade_history_resync",
                    "payload": history
                })
                print(f"Trade history resync sent ({len(history)} trades)")
            except Exception as e:
                print(f"⚠️ Failed to send trade history resync: {e}")
            
            print("State synchronization complete.")
        else:
            # Send initial state to this specific connection (not broadcast)
            try:
                await websocket.send_json({"type": "status_update", "payload": {
                    "connection": "DISCONNECTED", "mode": "NOT STARTED", "is_running": False,
                    "indexPrice": 0, "trend": "---", "indexName": "INDEX"
                }})
                print("Initial state sent to new client.")
            except Exception as send_err:
                print(f"[WARNING] Failed to send initial state: {send_err}")

        # Main message loop
        while True:
            try:
                # Set a longer timeout to accommodate trade execution (which can take 1-2 seconds)
                # Plus some buffer for network delays
                data = await asyncio.wait_for(websocket.receive_text(), timeout=300.0)
                message = json.loads(data)
                
                if message.get("type") == "ping":
                    # Update ping metadata
                    manager.update_ping_metadata(websocket)
                    # Send pong response
                    await websocket.send_text('{"type": "pong"}')
                    continue
                
                if message.get("type") == "add_to_watchlist":
                    payload = message.get("payload", {})
                    if service.strategy_instance:
                        await service.strategy_instance.add_to_watchlist(payload.get("side"), payload.get("strike"))
            
            except asyncio.TimeoutError:
                # No message received for 300 seconds - send a ping to check if client is alive
                try:
                    await asyncio.wait_for(websocket.send_text('{"type": "ping"}'), timeout=5.0)
                except asyncio.TimeoutError:
                    print("[WARNING] Ping send timeout, closing connection")
                    break
                except Exception:
                    # If we can't send ping, connection is dead
                    print("⚠️ Failed to send ping, closing connection")
                    break
            except json.JSONDecodeError as e:
                print(f"[WARNING] Invalid JSON received: {e}")
                continue
    
    except WebSocketDisconnect:
        duration = asyncio.get_event_loop().time() - conn_start_time
        print(f"WebSocket disconnected normally (duration: {duration:.1f}s)")
        await manager.disconnect(websocket)
    except RuntimeError as e:
        if "not connected" in str(e).lower():
            print(f"WebSocket closed by client")
        else:
            print(f"⚠️ WebSocket runtime error: {e}")
        await manager.disconnect(websocket)
    except Exception as e:
        duration = asyncio.get_event_loop().time() - conn_start_time
        print(f"❌ Error in websocket endpoint (duration: {duration:.1f}s): {e}")
        await manager.disconnect(websocket)

# ===== OPTION EXPIRY ENDPOINTS =====
@app.get("/api/expiries/{index_name}")
async def get_available_expiries(index_name: str, service: TradingBotService = Depends(get_bot_service)):
    """Get all available expiries for the selected index"""
    try:
        # Validate index name
        if index_name not in ['NIFTY', 'BANKNIFTY', 'SENSEX']:
            raise HTTPException(status_code=400, detail=f"Invalid index: {index_name}")
        
        from datetime import date
        from core.broker_factory import broker as kite
        import asyncio
        
        # Determine exchange
        exchange = "NFO" if index_name in ["NIFTY", "BANKNIFTY"] else "BFO"
        
        try:
            # Strategy 1: Try to use cached instruments from running strategy instance
            if service.strategy_instance and hasattr(service.strategy_instance, 'option_instruments'):
                instruments = service.strategy_instance.option_instruments
                
                # If loaded instruments are for a different index, we need to fetch fresh
                if instruments and len(instruments) > 0:
                    first_inst_index = instruments[0].get('name', '')
                    if first_inst_index == index_name:
                        # We can use these cached instruments
                        today = date.today()
                        expiries = set()
                        
                        for inst in instruments:
                            if inst.get('expiry') and inst['expiry'] >= today:
                                expiries.add(inst['expiry'])
                        
                        sorted_expiries = sorted(list(expiries))
                        formatted_expiries = [exp.strftime('%Y-%m-%d') for exp in sorted_expiries]
                        
                        return {
                            "index": index_name,
                            "expiries": formatted_expiries,
                            "count": len(formatted_expiries),
                            "source": "cached"
                        }
            
            # Strategy 2: Fetch fresh from Kite API with extended timeout
            try:
                instruments = await asyncio.wait_for(
                    kite.instruments(exchange),
                    timeout=45.0  # Extended timeout for initial load
                )
                
                # Filter for the selected index and get unique expiries
                today = date.today()
                expiries = set()
                
                for inst in instruments:
                    if inst['name'] == index_name and inst.get('expiry'):
                        expiry_date = inst['expiry']
                        # Only include future expiries
                        if expiry_date >= today:
                            expiries.add(expiry_date)
                
                # Sort expiries and format as strings
                sorted_expiries = sorted(list(expiries))
                formatted_expiries = [exp.strftime('%Y-%m-%d') for exp in sorted_expiries]
                
                return {
                    "index": index_name,
                    "expiries": formatted_expiries,
                    "count": len(formatted_expiries),
                    "source": "fresh"
                }
                
            except asyncio.TimeoutError:
                raise HTTPException(
                    status_code=504,
                    detail=f"Instrument loading timed out. This can happen when the broker API is slow. Try starting the bot first (it caches instruments) or try again in a moment."
                )
                
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to fetch instruments: {str(e)}")
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting expiries: {str(e)}")

# ===== USER MANAGEMENT ENDPOINTS =====
@app.get("/api/users")
async def get_users():
    """Get list of all users (without sensitive credentials)"""
    if BROKER_NAME == "kotak":
        try:
            with open("broker_config.json", "r") as f:
                cfg = json.load(f)
            labels = ["Primary", "Secondary", "Tertiary", "Quaternary"]
            kotak_users = cfg.get("kotak_users", [])
            # Fallback: single-user config
            if not kotak_users:
                kotak_users = [{
                    "id": "user1",
                    "name": cfg.get("kotak_user_name", "Kotak User"),
                    "ucc": cfg.get("kotak_ucc", ""),
                    "description": "Primary account"
                }]
            users = [
                {
                    "id": u["id"],
                    "name": u["name"],
                    "description": labels[i] + " account" if i < len(labels) else f"Account {i+1}"
                }
                for i, u in enumerate(kotak_users)
            ]
            return {
                "users": users,
                "active_user": cfg.get("kotak_active_user", "user1")
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error loading Kotak users: {str(e)}")
    else:
        try:
            with open("user_profiles.json", "r") as f:
                data = json.load(f)
            users = [
                {
                    "id": u["id"],
                    "name": u["name"],
                    "description": u.get("description", "")
                }
                for u in data.get("users", [])
            ]
            return {
                "users": users,
                "active_user": data.get("active_user")
            }
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="user_profiles.json not found.")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error loading users: {str(e)}")

@app.post("/api/users/switch/{user_id}")
async def switch_user(user_id: str):
    """Switch to a different user (requires bot restart to apply)"""
    cooldown_check("user_switch", cooldown_seconds=2.0)
    if BROKER_NAME == "kotak":
        try:
            with open("broker_config.json", "r") as f:
                cfg = json.load(f)
            kotak_users = cfg.get("kotak_users", [])
            user = next((u for u in kotak_users if u["id"] == user_id), None)
            if not user:
                raise HTTPException(status_code=404, detail=f"Kotak user '{user_id}' not found in broker_config.json")
            # Switch active user and load that user's credentials into top-level config
            cfg["kotak_active_user"] = user_id
            cfg["kotak_ucc"] = user.get("ucc", cfg.get("kotak_ucc", ""))
            cfg["kotak_mobile"] = user.get("mobile", cfg.get("kotak_mobile", ""))
            cfg["kotak_totp_secret"] = user.get("totp_secret", cfg.get("kotak_totp_secret", ""))
            cfg["kotak_mpin"] = user.get("mpin", cfg.get("kotak_mpin", ""))
            cfg["kotak_user_name"] = user.get("name", cfg.get("kotak_user_name", ""))
            with open("broker_config.json", "w") as f:
                json.dump(cfg, f, indent=4)
            return {
                "success": True,
                "message": f"Switched to Kotak user: {user['name']}",
                "active_user": user_id,
                "restart_required": True
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error switching Kotak user: {str(e)}")
    else:
        try:
            with open("user_profiles.json", "r") as f:
                data = json.load(f)
            user_exists = any(u["id"] == user_id for u in data.get("users", []))
            if not user_exists:
                raise HTTPException(status_code=404, detail=f"User '{user_id}' not found in user_profiles.json")
            user = next((u for u in data["users"] if u["id"] == user_id), None)
            user_name = user["name"] if user else user_id
            data["active_user"] = user_id
            with open("user_profiles.json", "w") as f:
                json.dump(data, f, indent=2)
            return {
                "success": True,
                "message": f"User switched to: {user_name}",
                "active_user": user_id,
                "restart_required": True
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error switching user: {str(e)}")

@app.get("/api/users/active")
async def get_active_user():
    """Get details of currently active user"""
    try:
        user_info = _get_active_user_info()
        if user_info.get("id") == "unknown":
             raise HTTPException(status_code=404, detail="Active user not found")
        return user_info
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting active user: {str(e)}")

# ===== KILL SWITCH STATUS ENDPOINT =====
@app.get("/api/kill_switch_status")
async def get_kill_switch_status():
    """Get current kill switch status for monitoring system health"""
    from core.kill_switch import kill_switch
    return kill_switch.get_status()

@app.post("/api/kill_switch_reset")
async def reset_kill_switch():
    """Manually reset the kill switch (use after fixing the underlying issue)"""
    from core.kill_switch import kill_switch
    kill_switch.manual_reset()
    return {"success": True, "message": "Kill switch has been manually reset"}

@app.post("/api/logout")
async def logout(service: TradingBotService = Depends(get_bot_service)):
    """Logout endpoint that stops bot, clears session, and disconnects all clients"""
    cooldown_check("logout", cooldown_seconds=2.0)

    try:
        print("Logout initiated - stopping bot and clearing session...")

        # 1. Stop the bot if running
        if service.ticker_manager_instance or service.strategy_instance or service.is_running:
            try:
                # Stop ticker first
                if service.ticker_manager_instance:
                    await service.ticker_manager_instance.stop()
                    print("Ticker stopped for logout")

                # Exit positions with timeout
                if service.strategy_instance and service.strategy_instance.position:
                    try:
                        await asyncio.wait_for(
                            service.strategy_instance.exit_position("User Logout"),
                            timeout=8.0
                        )
                        print("Positions exited for logout")
                    except asyncio.TimeoutError:
                        print("Position exit timed out during logout")
                    except Exception as e:
                        print(f"Error during position exit on logout: {e}")

                # Stop background tasks
                if service.uoa_scanner_task and not service.uoa_scanner_task.done():
                    service.uoa_scanner_task.cancel()
                if service.continuous_monitor_task and not service.continuous_monitor_task.done():
                    service.continuous_monitor_task.cancel()

                # Cleanup bot state
                await service._cleanup_bot_state()
                service.is_running = False

                print("Bot stopped successfully for logout")

            except Exception as e:
                print(f"Error stopping bot during logout: {e}")

        # 2. Clear the access token to invalidate session
        from core.broker_factory import clear_access_token
        try:
            clear_access_token()
            print("Access token cleared")
        except Exception as e:
            print(f"Error clearing access token: {e}")

        # 3. Broadcast logout message to all connected clients
        await manager.broadcast({
            "type": "logout_notification",
            "payload": {
                "message": "Bot stopped - User logged out",
                "redirect_url": "http://localhost:3001/"
            }
        })

        # 4. Send final disconnected status
        await manager.broadcast({
            "type": "status_update",
            "payload": {
                "connection": "DISCONNECTED",
                "mode": "NOT STARTED",
                "is_running": False,
                "is_paused": False,
                "indexPrice": 0,
                "trend": "---",
                "indexName": "INDEX"
            }
        })

        # Case 1: Logout button pressed
        await asyncio.to_thread(_send_bot_stop_email, "User Logged Out")

        # Give clients time to process messages before disconnecting
        await asyncio.sleep(1.0)

        # 5. Disconnect all WebSocket connections
        await manager.disconnect_all()
        print("All WebSocket connections closed")

        print("Logout completed successfully")
        return {
            "status": "success",
            "message": "Bot stopped - Logout successful",
            "redirect_url": "http://localhost:3001/"
        }

    except Exception as e:
        print(f"Error during logout: {e}")
        try:
            from core.broker_factory import clear_access_token
            clear_access_token()
            await manager.disconnect_all()
        except:
            pass

        return {
            "status": "error",
            "message": f"Logout completed with errors: {str(e)}",
            "redirect_url": "http://localhost:3001/"
        }

if __name__ == "__main__":
    import sys
    import signal
    
    # Prevent FastAPI from reading stdin which causes premature exit
    sys.stdin = open(os.devnull, 'r')
    
    # Add signal handlers to prevent unexpected shutdown
    def signal_handler(signum, frame):
        print(f"\n[DEBUG] Received signal {signum}")
        if signum == signal.SIGINT:
            print("[INFO] Ctrl+C pressed, shutting down...")
            sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, signal_handler)
    
    print("[DEBUG] Starting Uvicorn server...")
    try:
        uvicorn.run(
            "main:app",
            host="0.0.0.0",
            port=8000,
            reload=False,
            access_log=True
        )
    except KeyboardInterrupt:
        print("[INFO] Server stopped by user")
    except Exception as e:
        print(f"[ERROR] Server crashed: {e}")
        import traceback
        traceback.print_exc()
