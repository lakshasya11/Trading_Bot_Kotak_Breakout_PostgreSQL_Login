# backend/core/websocket_manager.py
from fastapi import WebSocket
import json
import numpy as np
import math
import asyncio
import logging
import time
from typing import List, Dict # Import List and Dict

# --- Custom JSON encoder remains the same ---
class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            if math.isnan(obj) or math.isinf(obj):
                return None
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        return super(CustomJSONEncoder, self).default(obj)

class ConnectionManager:
    def __init__(self):
        # CHANGED: Use a list to store multiple connections
        self.active_connections: List[WebSocket] = []
        self._cleanup_lock = asyncio.Lock()
        self._connection_metadata: Dict[WebSocket, dict] = {}
        self._connection_counter = 0
        # Debug log batching state
        self._debug_log_buffer = []
        self._last_debug_flush = 0
        self._debug_log_count = 0
        self._pending_sends = 0
        self._last_overflow_warning = 0
        # Start periodic flush task
        self._flush_task = None

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        # CHANGED: Add the new connection to the list with metadata
        self._connection_counter += 1
        conn_id = self._connection_counter
        self.active_connections.append(websocket)
        self._connection_metadata[websocket] = {
            'id': conn_id,
            'connected_at': time.time(),
            'last_ping': time.time(),
            'ping_count': 0
        }
        print(f"[OK] Frontend client connected (ID: {conn_id}). Total clients: {len(self.active_connections)}")

    async def disconnect(self, websocket: WebSocket):
        # CHANGED: Remove a specific connection from the list with async lock
        async with self._cleanup_lock:
            if websocket in self.active_connections:
                metadata = self._connection_metadata.get(websocket, {})
                conn_id = metadata.get('id', 'unknown')
                connected_duration = time.time() - metadata.get('connected_at', time.time())
                ping_count = metadata.get('ping_count', 0)
                
                self.active_connections.remove(websocket)
                if websocket in self._connection_metadata:
                    del self._connection_metadata[websocket]
                
                print(f"[DISC] Frontend client disconnected (ID: {conn_id}). "
                      f"Duration: {connected_duration:.1f}s, Pings: {ping_count}. "
                      f"Total clients: {len(self.active_connections)}")
    
    async def _periodic_debug_flush(self):
        """Periodically flush debug log buffer every 200ms"""
        while self.active_connections:
            try:
                await asyncio.sleep(0.2)  # Flush every 200ms
                await self._flush_debug_buffer()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[WARN] Error in periodic debug flush: {e}")
    
    async def _flush_debug_buffer(self):
        """Flush accumulated debug logs to WebSocket"""
        if not self._debug_log_buffer:
            return
        
        # Send only the latest 10 logs (drop older ones if buffer overflowed)
        logs_to_send = self._debug_log_buffer[-10:]
        dropped = self._debug_log_count - len(logs_to_send)
        
        # Create batched message
        batched_message = {
            "type": "debug_log_batch",
            "payload": {
                "logs": [msg.get('payload') for msg in logs_to_send],
                "dropped": dropped
            }
        }
        
        # Clear buffer and reset counter
        self._debug_log_buffer = []
        self._debug_log_count = 0
        self._last_debug_flush = time.time()
        
        # Send batched logs (bypass the batching logic in broadcast)
        try:
            json_message = json.dumps(batched_message, cls=CustomJSONEncoder)
            
            disconnected = []
            for connection in self.active_connections[:]:
                try:
                    self._pending_sends += 1
                    await asyncio.wait_for(connection.send_text(json_message), timeout=5.0)
                    self._pending_sends = max(0, self._pending_sends - 1)
                except Exception:
                    self._pending_sends = max(0, self._pending_sends - 1)
                    disconnected.append(connection)
            
            for conn in disconnected:
                await self.disconnect(conn)
        except Exception as e:
            print(f"[WARN] Error sending batched logs: {e}")

    async def broadcast(self, message: dict):
        if not self.active_connections:
            return
        
        try:
            json_message = json.dumps(message, cls=CustomJSONEncoder)
        except Exception as json_err:
            # Log serialization errors to console
            print(f"[WARN] JSON serialization error in broadcast: {json_err}")
            print(f"   Message type: {message.get('type', 'unknown')}")
            return
        
        # 🚦 INTELLIGENT RATE LIMITING: Different strategies per message type
        if not hasattr(self, '_pending_sends'):
            self._pending_sends = 0
            self._last_overflow_warning = 0
            self._debug_log_buffer = []
            self._last_debug_flush = 0
            self._debug_log_count = 0
        
        message_type = message.get('type', 'unknown')
        
        # Increased threshold to 100 to accommodate high-frequency updates (reduced lag)
        if self._pending_sends > 100:
            # Critical messages that must always be sent
            critical_types = ['status_update', 'trade_update', 'position_update', 'shutdown', 'time_sync', 
                            'daily_performance_update', 'trade_status_update', 'debug_log_batch', 'debug_log', 'play_sound',
                            'new_trade_log', 'batch_frame_update']  # 🚀 CRITICAL: Never drop trade/price updates
            
            if message_type not in critical_types:
                # Drop this message to prevent WebSocket timeout
                current_time = time.time()
                if current_time - self._last_overflow_warning > 5:
                    print(f"[WARN] WebSocket overflow prevention: Dropping {message_type} (pending: {self._pending_sends})")
                    self._last_overflow_warning = current_time
                return
        
        disconnected = []
        
        disconnected = []
        
        # Create a copy of the list to iterate over, in case we need to modify it
        for connection in self.active_connections[:]:
            try:
                self._pending_sends += 1
                # Reduced timeout from 15s to 5s to detect issues faster
                await asyncio.wait_for(connection.send_text(json_message), timeout=5.0)
                self._pending_sends = max(0, self._pending_sends - 1)
            except asyncio.TimeoutError:
                self._pending_sends = max(0, self._pending_sends - 1)
                print(f"[WARN] WebSocket send timeout for client (>5s)")
                disconnected.append(connection)
            except RuntimeError as e:
                self._pending_sends = max(0, self._pending_sends - 1)
                # Handle "WebSocket is not connected" errors gracefully
                if "not connected" in str(e).lower():
                    disconnected.append(connection)
                else:
                    print(f"[WARN] WebSocket runtime error: {e}")
                    disconnected.append(connection)
            except Exception as e:
                self._pending_sends = max(0, self._pending_sends - 1)
                # If sending fails, the client has likely disconnected. Remove them.
                logging.debug(f"Failed to send to client: {e}")
                disconnected.append(connection)
        
        # Clean up disconnected clients
        for conn in disconnected:
            await self.disconnect(conn)
    
    def update_ping_metadata(self, websocket: WebSocket):
        """Update ping metadata for a connection"""
        if websocket in self._connection_metadata:
            self._connection_metadata[websocket]['last_ping'] = time.time()
            self._connection_metadata[websocket]['ping_count'] += 1
    
    async def send_to_client(self, websocket: WebSocket, message: dict):
        """Send a message to a specific client"""
        try:
            json_message = json.dumps(message, cls=CustomJSONEncoder)
            await websocket.send_text(json_message)
        except Exception as e:
            logging.debug(f"Failed to send to specific client: {e}")
            await self.disconnect(websocket)

    async def disconnect_all(self):
        """Gracefully disconnect all clients"""
        async with self._cleanup_lock:
            for connection in self.active_connections.copy():
                try:
                    await connection.send_json({
                        "type": "shutdown",
                        "payload": {"reason": "Server shutting down"}
                    })
                    await connection.close(code=1000, reason="Server shutdown")
                except Exception as e:
                    logging.warning(f"Error closing connection: {e}")
            self.active_connections.clear()
            print("All WebSocket connections closed gracefully.")

    async def close(self):
        """Forcefully closes all active WebSocket connections."""
        async with self._cleanup_lock:
            for connection in self.active_connections[:]:
                try:
                    await connection.close()
                except Exception as e:
                    logging.warning(f"Error forcefully closing connection: {e}")
                if connection in self.active_connections:
                    self.active_connections.remove(connection)
            self.active_connections.clear()
            print("All WebSocket connections closed by server.")

manager = ConnectionManager()