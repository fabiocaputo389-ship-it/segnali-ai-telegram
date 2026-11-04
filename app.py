import os
import sqlite3
from flask import Flask, render_template_string, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'chiave_segreta_default_molto_sicura')

# Funzione per inizializzare il database SQLite
def init_db():
    conn = sqlite3.connect('trading_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

# Eseguiamo l'inizializzazione all'avvio
init_db()

# --- ROTTE DELL'APPLICAZIONE ---

@app.route('/')
def home():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template_string('''
        <!DOCTYPE html>
        <html lang="it">
        <head>
            <meta charset="UTF-8">
            <title>Dashboard - Segnali AI Telegram</title>
            <style>
                body { font-family: Arial, sans-serif; background-color: #0f172a; color: #f8fafc; margin: 0; padding: 20px; }
                .container { max-width: 800px; margin: 40px auto; background: #1e293b; padding: 30px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
                h1 { color: #38bdf8; }
                a { color: #f43f5e; text-decoration: none; font-weight: bold; }
                a:hover { text-decoration: underline; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🚀 Benvenuto nella Dashboard, {{ username }}!</h1>
                <p>Il tuo bot di segnali Telegram è attivo e configurato correttamente su Render.</p>
                <br>
                <a href="{{ url_for('logout') }}">Disconnettiti (Logout)</a>
            </div>
        </body>
        </html>
    ''', username=session['username'])

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = generate_password_hash(request.form.get('password'))
        try:
            conn = sqlite3.connect('trading_bot.db')
            cursor = conn.cursor()
            cursor.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, password))
            conn.commit()
            conn.close()
            flash('Registrazione completata! Effettua il login.')
            return redirect(url_for('login'))
        except Exception:
            flash('Username già esistente o errore di salvataggio.')

    content = '''
    <div style="max-width: 380px; margin: 50px auto; background: #1e293b; padding: 20px; border-radius: 8px; color: #fff; font-family: Arial;">
        <h3 style="color: #38bdf8; text-align: center;">Registrazione VIP</h3>
        <form method="POST">
            <div style="margin-bottom: 15px;">
                <label>Username</label><br>
                <input type="text" name="username" required style="width: 100%; padding: 8px; margin-top: 5px; box-sizing: border-box;">
            </div>
            <div style="margin-bottom: 15px;">
                <label>Password</label><br>
                <input type="password" name="password" required style="width: 100%; padding: 8px; margin-top: 5px; box-sizing: border-box;">
            </div>
            <button type="submit" style="width: 100%; background: #38bdf8; color: #0f172a; border: none; padding: 10px; font-weight: bold; cursor: pointer; border-radius: 4px;">Registrati</button>
        </form>
        <p style="text-align: center; margin-top: 15px;"><a href="/login" style="color: #38bdf8;">Hai già un account? Accedi</a></p>
    </div>
    '''
    return render_template_string(content)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        conn = sqlite3.connect('trading_bot.db')
        cursor = conn.cursor()
        cursor.execute('SELECT id, password FROM users WHERE username = ?', (username,))
        user = cursor.fetchone()
        conn.close()
        
        if user and check_password_hash(user[1], password):
            session['user_id'] = user[0]
            session['username'] = username
            flash('Login effettuato con successo!')
            return redirect(url_for('home'))
        
        flash('Credenziali non valide.')

    content = '''
    <div style="max-width: 380px; margin: 50px auto; background: #1e293b; padding: 20px; border-radius: 8px; color: #fff; font-family: Arial;">
        <h3 style="color: #38bdf8; text-align: center;">Accesso Piattaforma</h3>
        <form method="POST">
            <div style="margin-bottom: 15px;">
                <label>Username</label><br>
                <input type="text" name="username" required style="width: 100%; padding: 8px; margin-top: 5px; box-sizing: border-box;">
            </div>
            <div style="margin-bottom: 15px;">
                <label>Password</label><br>
                <input type="password" name="password" required style="width: 100%; padding: 8px; margin-top: 5px; box-sizing: border-box;">
            </div>
            <button type="submit" style="width: 100%; background: #38bdf8; color: #0f172a; border: none; padding: 10px; font-weight: bold; cursor: pointer; border-radius: 4px;">Accedi</button>
        </form>
        <p style="text-align: center; margin-top: 15px;"><a href="/register" style="color: #38bdf8;">Non hai un account? Registrati</a></p>
    </div>
    '''
    return render_template_string(content)

@app.route('/logout')
def logout():
    session.clear()
    flash('Logout effettuato.')
    return redirect(url_for('login'))

# Blocco di avvio per Render e test locali[span_1](start_span)[span_1](end_span)
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
