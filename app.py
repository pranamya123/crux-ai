from flask import Flask, render_template, request, jsonify, Response
import json
import os
import re
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)

# Initialize Supabase
SUPABASE_URL = os.getenv("NEXT_PUBLIC_SUPABASE_URL") or os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY") or os.getenv("ANON_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

SUBSCRIBER_LIMIT = 50

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

    try:
        # Check if email already exists
        response = supabase.table('subscribers').select('id').eq('email', email).execute()

        if response.data:
            return jsonify({'error': 'Already subscribed'}), 400

        # Enforce subscriber cap
        count_response = supabase.table('subscribers').select('id', count='exact').execute()
        current_count = count_response.count if count_response.count is not None else len(count_response.data or [])
        if current_count >= SUBSCRIBER_LIMIT:
            return jsonify({'error': 'Limit reached. Subscriptions are temporarily closed.'}), 403

        # Insert new subscriber
        supabase.table('subscribers').insert({'email': email}).execute()
        return jsonify({'success': True, 'message': 'Subscribed successfully'}), 201

    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({'error': 'Something went wrong'}), 500

@app.route('/unsubscribe', methods=['GET', 'POST'])
def unsubscribe():
    email = (request.values.get('email') or '').strip().lower()

    def page(title, message, ok=True):
        color = "#2e7d32" if ok else "#c62828"
        return f'''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Crux AI — {title}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#fafafa; color:#333; }}
        .container {{ max-width:560px; margin:80px auto; padding:40px 24px; background:#fff; border:1px solid #eee; border-radius:6px; text-align:center; }}
        h1 {{ font-family:Georgia, serif; font-weight:normal; color:#000; margin-bottom:16px; }}
        p {{ font-size:1.05em; line-height:1.6; color:{color}; }}
        a {{ color:#666; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>{title}</h1>
        <p>{message}</p>
        <p style="margin-top:30px;font-size:0.9em;color:#666;"><a href="/">Back to Crux AI</a></p>
    </div>
</body>
</html>'''

    if not email:
        return page("Unsubscribe", "Missing email address in the link.", ok=False), 400

    if not is_valid_email(email):
        return page("Unsubscribe", "That doesn't look like a valid email address.", ok=False), 400

    try:
        existing = supabase.table('subscribers').select('id').eq('email', email).execute()
        if not existing.data:
            return page("Already unsubscribed", f"{email} is not on the subscriber list.", ok=True)

        supabase.table('subscribers').delete().eq('email', email).execute()
        return page("Unsubscribed", f"{email} has been removed from the Crux AI list. You won't receive any more issues.", ok=True)
    except Exception as e:
        print(f"Error unsubscribing {email}: {e}")
        return page("Unsubscribe", "Something went wrong. Please try again later.", ok=False), 500


@app.route('/latest', methods=['GET'])
def latest_issue():
    html_path = os.path.join(PROJECT_DIR, 'latest_issue.html')
    meta_path = os.path.join(PROJECT_DIR, 'latest_issue_meta.json')

    if not os.path.exists(html_path):
        return Response(
            '''<!DOCTYPE html><html><head><meta charset="utf-8"><title>Crux AI — Latest issue</title></head>
<body style="font-family:Georgia,serif;max-width:560px;margin:80px auto;text-align:center;color:#333;">
<h1 style="font-weight:normal;">No issue published yet</h1>
<p>Check back after the next Thursday send. <a href="/" style="color:#666;">Subscribe</a> to be the first to get it.</p>
</body></html>''',
            status=404,
            mimetype='text/html',
        )

    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()

    subject = None
    sent_at = None
    if os.path.exists(meta_path):
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)
                subject = meta.get('subject')
                sent_at = meta.get('sent_at')
        except Exception:
            pass

    response = Response(html, mimetype='text/html')
    if subject:
        response.headers['X-Issue-Subject'] = subject
    if sent_at:
        response.headers['X-Issue-Sent-At'] = sent_at
    return response


@app.route('/admin', methods=['GET'])
def admin():
    try:
        response = supabase.table('subscribers').select('email').order('email').execute()
        subscribers = [row['email'] for row in response.data]
        emails_text = '\n'.join(subscribers) if subscribers else 'No subscribers yet.'

        return f'''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Crux AI - Subscribers</title>
            <style>
                body {{ font-family: system-ui; max-width: 800px; margin: 40px auto; padding: 20px; }}
                h1 {{ color: #333; }}
                textarea {{ width: 100%; height: 300px; font-family: monospace; border: 1px solid #ddd; padding: 10px; }}
                .count {{ color: #666; font-size: 0.9em; }}
            </style>
        </head>
        <body>
            <h1>Crux AI Subscribers</h1>
            <p class="count">Total: {len(subscribers)} subscribers</p>
            <textarea readonly>{emails_text}</textarea>
            <p style="color: #999; font-size: 0.85em;">Copy these emails to your .env RECIPIENT_EMAILS on Wednesday before the Thursday send.</p>
        </body>
        </html>
        '''
    except Exception as e:
        print(f"Error: {str(e)}")
        return "Error loading subscribers", 500

if __name__ == '__main__':
    app.run(debug=True)
