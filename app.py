from flask import Flask, render_template, request, jsonify
import json
import os
from datetime import datetime
import re

app = Flask(__name__)

SUBSCRIBERS_FILE = 'subscribers.json'

def load_subscribers():
    if os.path.exists(SUBSCRIBERS_FILE):
        with open(SUBSCRIBERS_FILE, 'r') as f:
            return json.load(f)
    return []

def save_subscribers(subscribers):
    with open(SUBSCRIBERS_FILE, 'w') as f:
        json.dump(subscribers, f, indent=2)

def is_valid_email(email):
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/subscribe', methods=['POST'])
def subscribe():
    data = request.json
    email = data.get('email', '').strip().lower()

    if not email:
        return jsonify({'error': 'Email is required'}), 400

    if not is_valid_email(email):
        return jsonify({'error': 'Invalid email format'}), 400

    subscribers = load_subscribers()

    if email in subscribers:
        return jsonify({'error': 'Already subscribed'}), 400

    subscribers.append(email)
    subscribers.sort()
    save_subscribers(subscribers)

    return jsonify({'success': True, 'message': 'Subscribed successfully'}), 201

@app.route('/admin', methods=['GET'])
def admin():
    subscribers = load_subscribers()
    emails_text = '\n'.join(subscribers) if subscribers else 'No subscribers yet.'
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>AI Weekly - Subscribers</title>
        <style>
            body {{ font-family: system-ui; max-width: 800px; margin: 40px auto; padding: 20px; }}
            h1 {{ color: #333; }}
            textarea {{ width: 100%; height: 300px; font-family: monospace; border: 1px solid #ddd; padding: 10px; }}
            .count {{ color: #666; font-size: 0.9em; }}
        </style>
    </head>
    <body>
        <h1>AI Weekly Subscribers</h1>
        <p class="count">Total: {len(subscribers)} subscribers</p>
        <textarea readonly>{emails_text}</textarea>
        <p style="color: #999; font-size: 0.85em;">Copy these emails to your .env RECIPIENT_EMAILS on Wednesday before the Thursday send.</p>
    </body>
    </html>
    '''

if __name__ == '__main__':
    app.run(debug=True)
