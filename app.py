#!/usr/bin/env python3
"""
Cocktail Menu Web App
A mobile-friendly web application to display available cocktails.
"""

import yaml
from flask import Flask, render_template
from pathlib import Path

app = Flask(__name__)

# Path to the cocktails YAML file
COCKTAILS_FILE = Path(__file__).parent / 'cocktails.yaml'


def load_cocktails():
    """Load cocktails from the YAML file."""
    with open(COCKTAILS_FILE, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    return data.get('cocktails', [])


@app.route('/')
def index():
    """Display the main cocktail menu page."""
    cocktails = load_cocktails()
    return render_template('index.html', cocktails=cocktails)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
