"""
main.py – Local development entry point.
Run with:  python main.py
"""
from app import app

if __name__ == '__main__':
    import os
    debug = os.environ.get('FLASK_DEBUG', '1') == '1'
    print('🚀 PayVanta AI running at http://127.0.0.1:5000\n')
    app.run(debug=debug, host='127.0.0.1', port=5000)
