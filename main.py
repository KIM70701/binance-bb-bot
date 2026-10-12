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
    # Render 환경에 맞춰서 0.0.0.0 으로 구동
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
        
        # 마지막 데이터(현재 실시간 진행 중인 캔들)는 제외하고 계산
        df = pd.DataFrame(data[:-1], columns=['Open_time', 'Open', 'High', 'Low', 'Close', 'Volume', 'Close_time', 'QA', 'Trades', 'Taker_buy_base', 'Taker_buy_quote', 'Ignore'])
        df['Close'] = df['Close'].astype(float)
        
        # 볼린저밴드 계산
        df['MA'] = df['Close'].rolling(window=PERIOD).mean()
        df['STD'] = df['Close'].rolling(window=PERIOD).std()
        df['Upper'] = df['MA'] + (df['STD'] * MULTIPLIER)
        df['Lower'] = df['MA'] - (df['STD'] * MULTIPLIER)
        
        # 가장 마지막 확정값(1봉 전) 저장
        prev_upper = round(df['Upper'].iloc[-1], 2)
        prev_lower = round(df['Lower'].iloc[-1], 2)
        
        print(f"[기준선 갱신] 1봉 전 상단: {prev_upper}, 하단: {prev_lower}")
    except Exception as e:
        print(f"BB 계산 중 에러 발생: {e}")

# === 바이낸스 웹소켓 메시지 처리 로직 ===
def on_message(ws, message):
    global alert_sent_this_candle, current_kline_start_time, prev_upper, prev_lower
    
    data = json.loads(message)
    if 'k' not in data:
        return
        
    kline = data['k']
    start_time = kline['t']
    open_price = float(kline['o'])
    current_price = float(kline['c'])
    
    # 1. 캔들이 새로 시작되었을 때 (상태 초기화 및 BB 기준선 갱신)
    if start_time != current_kline_start_time:
        current_kline_start_time = start_time
        alert_sent_this_candle = False
        print(f"--- 새로운 15분 캔들 시작됨 ---")
        update_bollinger_bands()

    # 2. 이번 캔들에서 이미 알람이 나갔다면 즉시 무시 (중복 알람 방지)
    if alert_sent_this_candle:
        return

    # 3. 돌파 감지 로직 (조건: 캔들 시가는 밴드 안쪽 & 현재가가 밴드 돌파)
    # 상단 돌파 (과매수 -> 매도 타점 확인용)
    if (open_price < prev_upper) and (current_price >= prev_upper):
        msg = f"🚀 <b>ETHUSDT 상단 돌파!</b>\n\n현재가: {current_price}\nBB 상단: {prev_upper}\n\n차트를 확인하세요!"
        send_telegram_message(msg)
        alert_sent_this_candle = True  # 플래그 잠금

    # 하단 이탈 (과매도 -> 매수 타점 확인용)
    elif (open_price > prev_lower) and (current_price <= prev_lower):
        msg = f"📉 <b>ETHUSDT 하단 이탈!</b>\n\n현재가: {current_price}\nBB 하단: {prev_lower}\n\n차트를 확인하세요!"
        send_telegram_message(msg)
        alert_sent_this_candle = True  # 플래그 잠금

def on_error(ws, error):
    print(f"웹소켓 에러 발생: {error}")

def on_close(ws, close_status_code, close_msg):
    print("웹소켓 연결 종료. 5초 뒤 재연결 시도...")

def on_open(ws):
    print("바이낸스 선물 웹소켓 실시간 연결 성공!")
    # 봇이 처음 켜질 때 최초 1회 기준선 계산
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
    # 1. Flask 웹 서버를 백그라운드 스레드에서 실행 (Render 접속 응답용)
    server_thread = threading.Thread(target=run_server)
    server_thread.daemon = True
    server_thread.start()
    
    # 2. 메인 스레드에서는 바이낸스 웹소켓 24시간 감시 루프 실행
    while True:
        try:
            run_websocket()
        except Exception as e:
            print(f"루프 에러: {e}")
        time.sleep(5)