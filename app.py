#!/usr/bin/env python3
"""
Cocktail Menu Web App
A mobile-friendly web application to display available cocktails.
"""

import json
import os
import yaml
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from functools import wraps
from pathlib import Path

app = Flask(__name__)
# Secret key for sessions (use environment variable in production)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

# Path to the cocktails YAML file (read-only, committed to git)
COCKTAILS_FILE = Path(__file__).parent / 'cocktails.yaml'
# Path to the ingredient state file (writable, not committed to git)
INGREDIENTS_STATE_FILE = Path(__file__).parent / 'ingredients_state.json'
# Path to the cocktail overrides file (writable, not committed to git)
COCKTAILS_OVERRIDES_FILE = Path(__file__).parent / 'cocktails_overrides.json'

# Admin password (use environment variable in production)
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin')


def login_required(f):
    """Decorator to require authentication for admin routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            # Check if it's an API request
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def load_ingredients_state():
    """Load the availability state of ingredients."""
    if INGREDIENTS_STATE_FILE.exists():
        try:
            with open(INGREDIENTS_STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_ingredients_state(state):
    """Save the availability state of ingredients."""
    with open(INGREDIENTS_STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)


def load_cocktail_overrides():
    """Load manual overrides for cocktails (True = force enable, False = force disable)."""
    if COCKTAILS_OVERRIDES_FILE.exists():
        try:
            with open(COCKTAILS_OVERRIDES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_cocktail_overrides(overrides):
    """Save manual overrides for cocktails."""
    with open(COCKTAILS_OVERRIDES_FILE, 'w', encoding='utf-8') as f:
        json.dump(overrides, f, indent=2)


def get_all_ingredients():
    """Get a list of all unique ingredients from all cocktails."""
    with open(COCKTAILS_FILE, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    cocktails = data.get('cocktails', [])
    
    ingredients = set()
    for cocktail in cocktails:
        for ingredient in cocktail.get('ingredients', []):
            ingredients.add(ingredient['name'])
    
    return sorted(list(ingredients))


def get_main_alcohol(cocktail):
    """Identify the main alcohol for a cocktail based on ingredients."""
    # Common alcohol keywords
    alcohol_keywords = ['rum', 'gin', 'vodka', 'whiskey', 'whisky', 'tequila', 'brandy', 
                        'cognac', 'bourbon', 'scotch', 'rye', 'mezcal', 'pisco', 'cachaça']
    
    # Find alcoholic ingredients (those containing alcohol keywords)
    alcoholic_ingredients = []
    for ingredient in cocktail.get('ingredients', []):
        ingredient_name_lower = ingredient['name'].lower()
        for keyword in alcohol_keywords:
            if keyword in ingredient_name_lower:
                # Try to get quantity as number (handle cases like "15 leaves")
                qty = ingredient.get('qty', 0)
                try:
                    qty_num = int(str(qty).split()[0])  # Get first number if it's "15 leaves"
                except (ValueError, AttributeError):
                    qty_num = 0
                alcoholic_ingredients.append((ingredient['name'], qty_num))
                break
    
    if not alcoholic_ingredients:
        return 'Other'
    
    # Sort by quantity (descending) and return the one with highest quantity
    alcoholic_ingredients.sort(key=lambda x: x[1], reverse=True)
    return alcoholic_ingredients[0][0]


def group_cocktails_by_alcohol(cocktails):
    """Group cocktails by main alcohol and sort them."""
    # Group cocktails by main alcohol
    grouped = {}
    for cocktail in cocktails:
        main_alcohol = get_main_alcohol(cocktail)
        if main_alcohol not in grouped:
            grouped[main_alcohol] = []
        grouped[main_alcohol].append(cocktail)
    
    # Sort within each group: enabled first (alphabetically), then disabled (alphabetically)
    for alcohol in grouped:
        grouped[alcohol].sort(key=lambda c: (
            not c.get('enabled', True),  # Enabled first (False < True)
            c['name'].lower()  # Then alphabetically
        ))
    
    # Sort alcohol groups alphabetically
    sorted_groups = sorted(grouped.items(), key=lambda x: x[0])
    
    return sorted_groups


def compute_cocktail_enabled(cocktail, ingredients_state, cocktail_overrides):
    """Compute if a cocktail should be enabled based on ingredients and overrides."""
    cocktail_name = cocktail['name']
    
    # Check for manual override first
    if cocktail_name in cocktail_overrides:
        return cocktail_overrides[cocktail_name]
    
    # Check if all ingredients are available
    # Default to available if not in state (True)
    for ingredient in cocktail.get('ingredients', []):
        ingredient_name = ingredient['name']
        if not ingredients_state.get(ingredient_name, True):
            return False  # At least one ingredient is unavailable
    
    return True  # All ingredients are available


def load_cocktails():
    """Load cocktails from the YAML file and compute enabled state."""
    with open(COCKTAILS_FILE, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    cocktails = data.get('cocktails', [])
    
    # Load ingredient state and cocktail overrides
    ingredients_state = load_ingredients_state()
    cocktail_overrides = load_cocktail_overrides()
    
    # Compute enabled state for each cocktail
    for cocktail in cocktails:
        cocktail['enabled'] = compute_cocktail_enabled(cocktail, ingredients_state, cocktail_overrides)
        # Store if it's a manual override
        cocktail['is_override'] = cocktail['name'] in cocktail_overrides
    
    return cocktails


@app.route('/health')
@app.route('/healthz')
def health():
    """Health check endpoint for Kubernetes liveness/readiness probes."""
    try:
        # Check if cocktails file is readable
        if not COCKTAILS_FILE.exists():
            return jsonify({'status': 'unhealthy', 'error': 'cocktails.yaml not found'}), 503
        
        # Try to load cocktails to verify the file is valid
        with open(COCKTAILS_FILE, 'r', encoding='utf-8') as f:
            yaml.safe_load(f)
        
        return jsonify({
            'status': 'healthy',
            'service': 'cocktail-menu'
        }), 200
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'error': str(e)
        }), 503


@app.route('/')
def index():
    """Display the main cocktail menu page."""
    cocktails = load_cocktails()
    # Group cocktails by main alcohol
    grouped_cocktails = group_cocktails_by_alcohol(cocktails)
    return render_template('index.html', grouped_cocktails=grouped_cocktails)


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page for admin access."""
    if request.method == 'POST':
        password = request.form.get('password', '')
        if password == ADMIN_PASSWORD:
            session['authenticated'] = True
            return redirect(url_for('admin'))
        else:
            return render_template('login.html', error='Incorrect password'), 401
    return render_template('login.html')


@app.route('/logout')
def logout():
    """Logout and clear session."""
    session.pop('authenticated', None)
    return redirect(url_for('index'))


@app.route('/admin')
@login_required
def admin():
    """Display the admin page to manage cocktails and ingredients."""
    cocktails = load_cocktails()
    # Group cocktails by main alcohol
    grouped_cocktails = group_cocktails_by_alcohol(cocktails)
    ingredients = get_all_ingredients()
    ingredients_state = load_ingredients_state()
    return render_template('admin.html', 
                         grouped_cocktails=grouped_cocktails,
                         ingredients=ingredients,
                         ingredients_state=ingredients_state)


@app.route('/api/state')
def get_state():
    """Get the current enabled/disabled state of all cocktails."""
    cocktails = load_cocktails()
    state = {c['name']: c.get('enabled', True) for c in cocktails}
    return jsonify(state)


@app.route('/api/toggle-ingredient', methods=['POST'])
@login_required
def toggle_ingredient():
    """Toggle the availability of an ingredient."""
    data = request.get_json()
    ingredient_name = data.get('name')
    
    if not ingredient_name:
        return jsonify({'error': 'Ingredient name is required'}), 400
    
    # Load current state
    ingredients_state = load_ingredients_state()
    # Toggle the state (default to True if not in state)
    current_state = ingredients_state.get(ingredient_name, True)
    new_state = not current_state
    ingredients_state[ingredient_name] = new_state
    
    # If ingredient is becoming available, clear overrides for cocktails that use it
    if new_state:  # Ingredient is now available
        # Load cocktails and overrides
        with open(COCKTAILS_FILE, 'r', encoding='utf-8') as f:
            yaml_data = yaml.safe_load(f)
        cocktails = yaml_data.get('cocktails', [])
        cocktail_overrides = load_cocktail_overrides()
        
        # Find cocktails that use this ingredient
        cocktails_to_check = [c for c in cocktails if any(
            ing['name'] == ingredient_name for ing in c.get('ingredients', [])
        )]
        
        # For each cocktail using this ingredient, check if override should be cleared
        for cocktail in cocktails_to_check:
            cocktail_name = cocktail['name']
            # Only clear override if cocktail has an override
            if cocktail_name in cocktail_overrides:
                # Check if all ingredients are now available
                all_available = all(
                    ingredients_state.get(ing['name'], True)
                    for ing in cocktail.get('ingredients', [])
                )
                # If all ingredients are available, remove the override
                if all_available:
                    del cocktail_overrides[cocktail_name]
        
        # Save updated overrides if any were removed
        save_cocktail_overrides(cocktail_overrides)
    
    # Save the updated ingredient state
    save_ingredients_state(ingredients_state)
    
    return jsonify({'success': True, 'available': new_state})

@app.route('/api/toggle-cocktail', methods=['POST'])
@login_required
def toggle_cocktail():
    """Toggle manual override for a cocktail."""
    data = request.get_json()
    cocktail_name = data.get('name')
    
    if not cocktail_name:
        return jsonify({'error': 'Cocktail name is required'}), 400
    
    # Verify cocktail exists
    with open(COCKTAILS_FILE, 'r', encoding='utf-8') as f:
        yaml_data = yaml.safe_load(f)
    cocktail_names = [c['name'] for c in yaml_data.get('cocktails', [])]
    
    if cocktail_name not in cocktail_names:
        return jsonify({'error': 'Cocktail not found'}), 404
    
    # Load current overrides and ingredients state
    cocktail_overrides = load_cocktail_overrides()
    ingredients_state = load_ingredients_state()
    
    # Get the cocktail to compute current enabled state
    cocktails = yaml_data.get('cocktails', [])
    cocktail = next((c for c in cocktails if c['name'] == cocktail_name), None)
    if not cocktail:
        return jsonify({'error': 'Cocktail not found'}), 404
    
    current_enabled = compute_cocktail_enabled(cocktail, ingredients_state, cocktail_overrides)
    
    # Toggle: set override to opposite of current enabled state
    cocktail_overrides[cocktail_name] = not current_enabled
    
    # Save the updated overrides
    save_cocktail_overrides(cocktail_overrides)
    
    return jsonify({
        'success': True, 
        'enabled': not current_enabled,
        'is_override': True
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
