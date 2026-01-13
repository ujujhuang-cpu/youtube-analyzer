# 安裝需要的套件：
# pip install flask flask-cors apscheduler requests yagmail

from flask import Flask, request, jsonify
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import requests
import yagmail
import json
import os
from datetime import datetime, timedelta
import csv
import io

app = Flask(__name__)
CORS(app)  # 允許跨域請求

# 設定檔案
DATA_FILE = 'schedules.json'
RESULTS_DIR = 'reports'

# 郵件設定（需要設定你的 Gmail 和應用程式密碼）
EMAIL_USER = 'your-email@gmail.com'  # 改成你的 Gmail
EMAIL_PASSWORD = 'your-app-password'  # Gmail 應用程式密碼

# 初始化排程器
scheduler = BackgroundScheduler()
scheduler.start()

# 載入/儲存排程資料
def load_schedules():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_schedules(schedules):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(schedules, f, ensure_ascii=False, indent=2)

# YouTube API 相關函數
def get_channel_id(name, api_key):
    url = f'https://www.googleapis.com/youtube/v3/search?part=snippet&type=channel&q={name}&key={api_key}&maxResults=1'
    response = requests.get(url)
    data = response.json()
    if data.get('items'):
        return data['items'][0]['snippet']['channelId'], data['items'][0]['snippet']['title']
    return None, None

def get_uploads_playlist(channel_id, api_key):
    url = f'https://www.googleapis.com/youtube/v3/channels?part=contentDetails&id={channel_id}&key={api_key}'
    response = requests.get(url)
    data = response.json()
    if data.get('items'):
        return data['items'][0]['contentDetails']['relatedPlaylists']['uploads']
    return None

def get_videos(playlist_id, months, api_key):
    items = []
    cutoff_date = datetime.utcnow() - timedelta(days=months * 30)
    next_token = None
    
    while True:
        url = f'https://www.googleapis.com/youtube/v3/playlistItems?part=contentDetails&maxResults=50&playlistId={playlist_id}&key={api_key}'
        if next_token:
            url += f'&pageToken={next_token}'
        
        response = requests.get(url)
        data = response.json()
        
        for item in data.get('items', []):
            video_id = item['contentDetails']['videoId']
            published_at = datetime.strptime(item['contentDetails']['videoPublishedAt'], '%Y-%m-%dT%H:%M:%SZ')
            if published_at >= cutoff_date:
                items.append((video_id, published_at))
        
        next_token = data.get('nextPageToken')
        if not next_token:
            break
    
    return items

def get_video_info(video_id, api_key):
    url = f'https://www.googleapis.com/youtube/v3/videos?part=snippet&id={video_id}&key={api_key}'
    response = requests.get(url)
    data = response.json()
    if data.get('items'):
        snippet = data['items'][0]['snippet']
        return snippet['title'], snippet['description']
    return None, None

def get_pinned_links(video_id, api_key):
    links = []
    try:
        url = f'https://www.googleapis.com/youtube/v3/commentThreads?part=snippet&videoId={video_id}&maxResults=20&key={api_key}'
        response = requests.get(url)
        data = response.json()
        
        for item in data.get('items', []):
            comment = item['snippet']['topLevelComment']['snippet']
            if comment.get('authorIsChannelOwner'):
                text = comment.get('textDisplay', '')
                import re
                matches = re.findall(r'(https?://[^\s]+)', text)
                links.extend(matches)
    except:
        pass
    return links

def analyze_channel(channel_name, months, api_key):
    """分析單一頻道"""
    print(f"開始分析頻道：{channel_name}")
    
    # 取得頻道 ID
    channel_id, channel_title = get_channel_id(channel_name, api_key)
    if not channel_id:
        print(f"找不到頻道：{channel_name}")
        return None, {}
    
    # 取得播放清單
    playlist_id = get_uploads_playlist(channel_id, api_key)
    if not playlist_id:
        print(f"無法取得播放清單：{channel_name}")
        return None, {}
    
    # 取得影片列表
    videos = get_videos(playlist_id, months, api_key)
    if not videos:
        print(f"沒有找到影片：{channel_name}")
        return channel_title, {}
    
    # 分析每個影片
    stats = {}
    for video_id, published_at in videos:
        title, description = get_video_info(video_id, api_key)
        if title:
            # 從描述中找連結
            import re
            desc_links = re.findall(r'(https?://[^\s]+)', description or '')
            comment_links = get_pinned_links(video_id, api_key)
            all_links = desc_links + comment_links
            
            for link in all_links:
                link = link.rstrip(',;)')
                if link not in stats:
                    stats[link] = {
                        'count': 1,
                        'videos': [video_id],
                        'titles': [title],
                        'dates': [published_at.strftime('%Y-%m-%d')]
                    }
                else:
                    stats[link]['count'] += 1
                    stats[link]['videos'].append(video_id)
                    stats[link]['titles'].append(title)
                    stats[link]['dates'].append(published_at.strftime('%Y-%m-%d'))
    
    return channel_title, stats

def run_analysis(schedule_id):
    """執行分析任務"""
    print(f"--- 執行排程 {schedule_id} 的分析任務 ---")
    
    schedules = load_schedules()
    schedule = next((s for s in schedules if s['id'] == schedule_id), None)
    
    if not schedule or not schedule['active']:
        print(f"排程 {schedule_id} 不存在或已停用")
        return
    
    # 分析所有頻道
    all_results = []
    for channel_name in schedule['channels']:
        print(f"正在分析頻道: {channel_name}...")
        channel_title, stats = analyze_channel(channel_name, schedule['months'], schedule['apiKey'])
        if channel_title and stats:
            for link, data in stats.items():
                all_results.append({
                    'channel': channel_title,
                    'link': link,
                    'count': data['count'],
                    'videos': ','.join(data['videos']),
                    'titles': ' | '.join(data['titles']),
                    'dates': ' | '.join(data['dates'])
                })
    
    # 檢查是否有結果
    if all_results:
        # 【重點：在這裡定義 filename】
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{RESULTS_DIR}/report_{schedule_id}_{timestamp}.csv"
        
        os.makedirs(RESULTS_DIR, exist_ok=True)
        
        print(f"分析完成，正在產生 CSV 檔案: {filename}")
        with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=['channel', 'link', 'count', 'videos', 'titles', 'dates'])
            writer.writeheader()
            writer.writerows(all_results)
        
        # 發送郵件 (filename 必須在 if all_results 裡面才拿得到)
        print(f"準備發送郵件報告至 {schedule['email']}...")
        send_email_report(schedule['email'], schedule['name'], filename, len(all_results))
        print(f"任務完全結束！")
    else:
        print("分析結束：沒有找到任何符合條件的業配連結，故不發送郵件。")

def send_email_report(to_email, schedule_name, csv_file, link_count):
    """發送郵件報告"""
    try:
        yag = yagmail.SMTP(EMAIL_USER, EMAIL_PASSWORD)
        
        subject = f"YouTube 業配分析報告 - {schedule_name}"
        body = f"""
        <h2>YouTube 業配分析報告</h2>
        <p>排程名稱：{schedule_name}</p>
        <p>分析時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        <p>找到業配連結：<strong>{link_count}</strong> 條</p>
        <p>詳細報告請查看附件 CSV 檔案。</p>
        <hr>
        <p style="color: #666; font-size: 12px;">此郵件由 YouTube 業配分析系統自動發送</p>
        """
        
        yag.send(
            to=to_email,
            subject=subject,
            contents=body,
            attachments=[csv_file]
        )
        print(f"郵件已發送到 {to_email}")
    except Exception as e:
        print(f"發送郵件失敗：{e}")

# API 路由
@app.route('/api/schedules', methods=['GET'])
def get_schedules():
    """取得所有排程"""
    schedules = load_schedules()
    # 不要返回 API 金鑰
    for s in schedules:
        s.pop('apiKey', None)
    return jsonify(schedules)

@app.route('/api/schedules', methods=['POST'])
def create_schedule():
    """建立新排程"""
    data = request.json
    
    # 驗證必填欄位
    required = ['name', 'apiKey', 'channels', 'frequency', 'email']
    if not all(field in data for field in required):
        return jsonify({'message': '缺少必填欄位'}), 400
    
    schedules = load_schedules()
    
    # 建立新排程
    import uuid
    schedule = {
        'id': str(uuid.uuid4()),
        'name': data['name'],
        'apiKey': data['apiKey'],
        'channels': data['channels'],
        'months': data.get('months', 6),
        'frequency': data['frequency'],
        'sendTime': data.get('sendTime', '09:00'),
        'email': data['email'],
        'active': True,
        'createdAt': datetime.now().isoformat()
    }
    
    schedules.append(schedule)
    save_schedules(schedules)
    
    # 設定排程任務
    setup_schedule_job(schedule)
    
    return jsonify({'message': '建立成功', 'id': schedule['id']}), 201

@app.route('/api/schedules/<schedule_id>/toggle', methods=['POST'])
def toggle_schedule(schedule_id):
    """啟用/停用排程"""
    data = request.json
    schedules = load_schedules()
    
    schedule = next((s for s in schedules if s['id'] == schedule_id), None)
    if not schedule:
        return jsonify({'message': '找不到排程'}), 404
    
    schedule['active'] = data['active']
    save_schedules(schedules)
    
    # 更新排程任務
    if data['active']:
        setup_schedule_job(schedule)
    else:
        scheduler.remove_job(schedule_id, jobstore=None)
    
    return jsonify({'message': '更新成功'})

@app.route('/api/schedules/<schedule_id>', methods=['DELETE'])
def delete_schedule(schedule_id):
    """刪除排程"""
    schedules = load_schedules()
    schedules = [s for s in schedules if s['id'] != schedule_id]
    save_schedules(schedules)
    
    # 移除排程任務
    try:
        scheduler.remove_job(schedule_id, jobstore=None)
    except:
        pass
    
    return jsonify({'message': '刪除成功'})
    
@app.route('/api/schedules/<schedule_id>/run', methods=['POST'])
def run_now(schedule_id):
    """立即執行分析任務"""
    # 這裡我們使用背景執行，避免前端網頁卡住
    scheduler.add_job(
        func=run_analysis,
        trigger='date',  # 'date' 觸發器代表只執行一次，時間為現在
        args=[schedule_id],
        id=f"once_{schedule_id}_{datetime.now().timestamp()}"
    )
    return jsonify({'message': '任務已啟動，分析完成後將寄送郵件報告！'})
    
def setup_schedule_job(schedule):
    """設定排程任務"""
    hour, minute = schedule['sendTime'].split(':')
    
    if schedule['frequency'] == 'daily':
        trigger = CronTrigger(hour=int(hour), minute=int(minute))
    elif schedule['frequency'] == 'weekly':
        trigger = CronTrigger(day_of_week='mon', hour=int(hour), minute=int(minute))
    else:  # monthly
        trigger = CronTrigger(day=1, hour=int(hour), minute=int(minute))
    
    try:
        scheduler.add_job(
            func=run_analysis,
            trigger=trigger,
            id=schedule['id'],
            args=[schedule['id']],
            replace_existing=True
        )
        print(f"已設定排程任務：{schedule['id']}")
    except Exception as e:
        print(f"設定排程失敗：{e}")

# 啟動時載入所有排程
def load_all_schedules():
    schedules = load_schedules()
    for schedule in schedules:
        if schedule['active']:
            setup_schedule_job(schedule)
    print(f"已載入 {len(schedules)} 個排程")

if __name__ == '__main__':
    os.makedirs(RESULTS_DIR, exist_ok=True)
    load_all_schedules()
    app.run(host='0.0.0.0', port=3000, debug=True)
