import os
from flask import Flask, jsonify, render_template

app = Flask(__name__)

# (Qui ci sono tutte le tue rotte e la logica dello scanner...)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
