from flask import Flask, render_template, request, jsonify
import os
import re
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Initialize Supabase
SUPABASE_URL = os.getenv("NEXT_PUBLIC_SUPABASE_URL") or os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY") or os.getenv("ANON_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

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

        # Insert new subscriber
        supabase.table('subscribers').insert({'email': email}).execute()
        return jsonify({'success': True, 'message': 'Subscribed successfully'}), 201

    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({'error': 'Something went wrong'}), 500

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
    except Exception as e:
        print(f"Error: {str(e)}")
        return "Error loading subscribers", 500

if __name__ == '__main__':
    app.run(debug=True)
