import json
import time
import threading
import requests
import pandas as pd
import websocket
from flask import Flask
import os

# === 환경 변수 설정 (Render에서 입력받음) ===
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '여기에_토큰입력')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '여기에_챗ID입력')
SYMBOL = 'ethusdt'
INTERVAL = '15m'
PERIOD = 45
MULTIPLIER = 2

# === 글로벌 상태 변수 ===
alert_sent_this_candle = False
current_kline_start_time = 0
prev_upper = 0.0
prev_lower = 0.0

# === Flask 웹 서버 (Render Sleep 방지용) ===
app = Flask(__name__)

@app.route('/')
def keep_alive():
    return "Bot is running perfectly!"

def run_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# === 텔레그램 발송 함수 ===
def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, json=payload)
        print("텔레그램 알람 전송 완료")
    except Exception as e:
        print(f"텔레그램 전송 실패: {e}")

# === 1봉 전 확정된 볼린저밴드 값 계산 ===
def update_bollinger_bands():
    global prev_upper, prev_lower
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={SYMBOL.upper()}&interval={INTERVAL}&limit=100"
    try:
        res = requests.get(url)
        data = res.json()
        
        # === [진단 1] REST API 원본 응답 확인 ===
        print(f"[REST 원본 응답] {str(data)[:200]}") 
        
        # 데이터가 리스트(정상적인 차트 데이터)가 아니라 딕셔너리(에러 메시지) 형태일 경우 차단 사유 출력
        if isinstance(data, dict):
            print(f"❌ 바이낸스 API 거절 사유 확인됨: {data}")
            return
            
        df = pd.DataFrame(data[:-1], columns=['Open_time', 'Open', 'High', 'Low', 'Close', 'Volume', 'Close_time', 'QA', 'Trades', 'Taker_buy_base', 'Taker_buy_quote', 'Ignore'])
        df['Close'] = df['Close'].astype(float)
        
        df['MA'] = df['Close'].rolling(window=PERIOD).mean()
        df['STD'] = df['Close'].rolling(window=PERIOD).std()
        df['Upper'] = df['MA'] + (df['STD'] * MULTIPLIER)
        df['Lower'] = df['MA'] - (df['STD'] * MULTIPLIER)
        
        prev_upper = round(df['Upper'].iloc[-1], 2)
        prev_lower = round(df['Lower'].iloc[-1], 2)
        
        print(f"[기준선 갱신] 1봉 전 상단: {prev_upper}, 하단: {prev_lower}")
    except Exception as e:
        print(f"BB 계산 중 에러 발생: {e}")

# === 바이낸스 웹소켓 메시지 처리 로직 ===
def on_message(ws, message):
    global alert_sent_this_candle, current_kline_start_time, prev_upper, prev_lower
    
    # === [진단 2] 웹소켓 원본 메시지 수신 확인 ===
    print(f"[WS 수신 데이터] {str(message)[:200]}")
    
    data = json.loads(message)
    if 'k' not in data:
        return
        
    kline = data['k']
    start_time = kline['t']
    open_price = float(kline['o'])
    current_price = float(kline['c'])
    
    if start_time != current_kline_start_time:
        current_kline_start_time = start_time
        alert_sent_this_candle = False
        print(f"--- 새로운 15분 캔들 시작됨 ---")
        update_bollinger_bands()

    if alert_sent_this_candle:
        return

    if (open_price < prev_upper) and (current_price >= prev_upper):
        msg = f"🚀 <b>ETHUSDT 상단 돌파!</b>\n\n현재가: {current_price}\nBB 상단: {prev_upper}\n\n차트를 확인하세요!"
        send_telegram_message(msg)
        alert_sent_this_candle = True

    elif (open_price > prev_lower) and (current_price <= prev_lower):
        msg = f"📉 <b>ETHUSDT 하단 이탈!</b>\n\n현재가: {current_price}\nBB 하단: {prev_lower}\n\n차트를 확인하세요!"
        send_telegram_message(msg)
        alert_sent_this_candle = True

def on_error(ws, error):
    print(f"웹소켓 에러 발생: {error}")

def on_close(ws, close_status_code, close_msg):
    print(f"웹소켓 연결 종료 (코드: {close_status_code}, 사유: {close_msg}). 5초 뒤 재연결...")

def on_open(ws):
    print("바이낸스 선물 웹소켓 실시간 연결 성공!")
    update_bollinger_bands()

# === 웹소켓 실행 무한 루프 ===
def run_websocket():
    stream_url = f"wss://fstream.binance.com/ws/{SYMBOL}@kline_{INTERVAL}"
    ws = websocket.WebSocketApp(
        stream_url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    ws.run_forever()

if __name__ == "__main__":
    server_thread = threading.Thread(target=run_server)
    server_thread.daemon = True
    server_thread.start()
    
    while True:
        try:
            run_websocket()
        except Exception as e:
            print(f"루프 에러: {e}")
        time.sleep(5)
