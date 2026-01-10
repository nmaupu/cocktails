#!/usr/bin/env python3
"""
Cocktail Menu Web App
A mobile-friendly web application to display available cocktails.
"""

import json
import os
import yaml
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_from_directory
from functools import wraps
from pathlib import Path

app = Flask(__name__)
# Secret key for sessions (use environment variable in production)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

# Path to the cocktails YAML file (configurable via environment variable)
# Defaults to bundled file for local development, or external config in Kubernetes
COCKTAILS_FILE = Path(os.environ.get('COCKTAILS_FILE_PATH', Path(__file__).parent / 'cocktails.yaml'))
# Directory for state files (writable, not committed to git)
# Defaults to /data for Kubernetes PVC, falls back to app directory for local development
STATE_DIR = Path(os.environ.get('STATE_DIR', '/data' if Path('/data').exists() else Path(__file__).parent))
# Ensure state directory exists
STATE_DIR.mkdir(parents=True, exist_ok=True)
# Path to the ingredient state file (writable, not committed to git)
INGREDIENTS_STATE_FILE = STATE_DIR / 'ingredients_state.json'
# Path to the cocktail overrides file (writable, not committed to git)
COCKTAILS_OVERRIDES_FILE = STATE_DIR / 'cocktails_overrides.json'

# Admin password (use environment variable in production)
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin')

# Ingredients to hide from guest page display
HIDDEN_INGREDIENTS_GUEST = ['water', 'simple syrup', 'sugar', 'salt']

# Ingredients to hide from admin checklist
HIDDEN_INGREDIENTS_ADMIN = ['water', 'salt']


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


def get_ingredient_name(ingredient, lang='en'):
    """Get the ingredient name in the specified language.
    
    Ingredient names are always dicts with at least 'en' key.
    If 'fr' is missing, it defaults to 'en'.
    """
    name = ingredient.get('name', {})
    # Handle legacy string format (shouldn't happen after normalization)
    if isinstance(name, str):
        name = {'en': name}
    # Ensure we have a dict with at least 'en'
    if not isinstance(name, dict) or 'en' not in name:
        return ''
    # If requested language is not available, fall back to English
    if lang == 'fr' and 'fr' not in name:
        return name.get('en', '')
    return name.get(lang, name.get('en', ''))


def get_ingredient_name_en(ingredient):
    """Get the English ingredient name (used as key for state management)."""
    return get_ingredient_name(ingredient, 'en')


def get_category_name(category, lang='en'):
    """Get the category name in the specified language.
    
    Categories are always dicts with at least 'en' key.
    If 'fr' is missing, it defaults to 'en'.
    """
    # Handle legacy string format (shouldn't happen after normalization)
    if isinstance(category, str):
        category = {'en': category}
    # Ensure we have a dict with at least 'en'
    if not isinstance(category, dict) or 'en' not in category:
        return ''
    # If requested language is not available, fall back to English
    if lang == 'fr' and 'fr' not in category:
        return category.get('en', '')
    return category.get(lang, category.get('en', ''))


def get_all_ingredients():
    """Get a list of all unique ingredients from all cocktails (using English names as keys)."""
    with open(COCKTAILS_FILE, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    cocktails = data.get('cocktails', [])

    ingredients = set()
    for cocktail in cocktails:
        for ingredient in cocktail.get('ingredients', []):
            # Use English name as the key for state management
            ingredient_name = get_ingredient_name(ingredient, 'en')
            ingredients.add(ingredient_name)

    return sorted(list(ingredients))


def get_main_alcohol(cocktail, use_override=True, lang='en'):
    """Identify the main alcohol for a cocktail based on ingredients or override."""
    # Check for manual category override in YAML first (case-insensitive)
    if use_override:
        for key in cocktail.keys():
            if key.lower() == 'category':
                category = cocktail[key]
                return get_category_name(category, lang)
    
    # Whitelist of actual alcoholic ingredients (case-insensitive matching)
    alcohol_whitelist = [
        'rum', 'white rum', 'dark rum', 'aged rum', 'spiced rum',
        'gin', 'old tom gin', 'plymouth gin', 'pink gin',
        'vodka',
        'whiskey', 'whisky', 'rye whiskey', 'bourbon', 'scotch whisky', 'irish whiskey', 'japanese whisky',
        'tequila', 'mezcal',
        'brandy', 'cognac', 'armagnac',
        'cachaça', 'pisco',
        'aperol', 'campari', 'cointreau',
        'prosecco', 'champagne', 'sparkling wine',
        'red port', 'port', 'sherry',
        'liqueur', 'liqueurs'
    ]

    # Find the first alcoholic ingredient (only those in the whitelist)
    # Process ingredients in order and return the first one that matches
    for ingredient in cocktail.get('ingredients', []):
        ingredient_name = get_ingredient_name(ingredient, 'en')
        ingredient_name_lower = ingredient_name.lower()

        # Check if ingredient matches any alcohol in whitelist
        matched_alcohol_whitelist = None

        # Check if ingredient name contains a whitelisted alcohol (only check one direction)
        for alcohol in sorted(alcohol_whitelist, key=len, reverse=True):  # Longer names first for better matching
            if alcohol in ingredient_name_lower:
                matched_alcohol_whitelist = alcohol
                break

        # Only process if we found a match in the whitelist
        if matched_alcohol_whitelist:
            # Try to get quantity as number (handle cases like "15 leaves", "2 dashes", etc.)
            qty = ingredient.get('qty', 0)
            qty_num = 0
            try:
                qty_str = str(qty).lower()
                # Handle various quantity formats
                if 'dash' in qty_str or 'drop' in qty_str or 'teaspoon' in qty_str or 'bar spoon' in qty_str:
                    # For dashes/drops, use a small number for comparison
                    qty_num = 1
                elif 'top' in qty_str or 'splash' in qty_str or 'on top' in qty_str:
                    qty_num = 0  # Don't count these as main alcohol
                else:
                    # Try to extract number (handle both int and float)
                    qty_parts = str(qty).split()
                    if qty_parts:
                        # Try float first to handle decimals like 22.5
                        try:
                            qty_num = float(qty_parts[0])
                        except ValueError:
                            qty_num = int(qty_parts[0])
            except (ValueError, AttributeError):
                qty_num = 0

            # Return the first matching ingredient with meaningful quantity
            if qty_num > 0:
                # Capitalize the matched alcohol name for display
                display_name = matched_alcohol_whitelist.title()
                return display_name

    # If no alcoholic ingredient found, return 'Other' (translate based on lang)
    if lang == 'fr':
        return 'Autre'
    return 'Other'


def group_cocktails_by_alcohol(cocktails, lang='en'):
    """Group cocktails by main alcohol and sort them."""
    # Group cocktails by main alcohol
    grouped = {}
    for cocktail in cocktails:
        main_alcohol = get_main_alcohol(cocktail, lang=lang)
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
        # Use English name as key for state lookup
        ingredient_name = get_ingredient_name(ingredient, 'en')
        if not ingredients_state.get(ingredient_name, True):
            return False  # At least one ingredient is unavailable

    return True  # All ingredients are available


def load_cocktails(lang='en'):
    """Load cocktails from the YAML file and compute enabled state."""
    with open(COCKTAILS_FILE, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    cocktails = data.get('cocktails', [])

    # Load ingredient state and cocktail overrides
    ingredients_state = load_ingredients_state()
    cocktail_overrides = load_cocktail_overrides()

    # Compute enabled state for each cocktail and translate ingredient names
    for cocktail in cocktails:
        cocktail['enabled'] = compute_cocktail_enabled(cocktail, ingredients_state, cocktail_overrides)
        # Store if it's a manual override
        cocktail['is_override'] = cocktail['name'] in cocktail_overrides
        # Translate ingredient names for display and add English name for state lookup
        for ingredient in cocktail.get('ingredients', []):
            ingredient['display_name'] = get_ingredient_name(ingredient, lang)
            ingredient['name_en'] = get_ingredient_name_en(ingredient)

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
    # Get language from session, default to 'fr'
    lang = session.get('lang', 'fr')
    cocktails = load_cocktails(lang)
    # Filter to show only enabled cocktails
    enabled_cocktails = [c for c in cocktails if c.get('enabled', True)]
    # Group cocktails by main alcohol
    grouped_cocktails = group_cocktails_by_alcohol(enabled_cocktails, lang=lang)
    return render_template('index.html', 
                         grouped_cocktails=grouped_cocktails,
                         hidden_ingredients=HIDDEN_INGREDIENTS_GUEST,
                         lang=lang)


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
    # Get language from session, default to 'fr'
    lang = session.get('lang', 'fr')
    cocktails = load_cocktails(lang)
    # Group cocktails by main alcohol
    grouped_cocktails = group_cocktails_by_alcohol(cocktails, lang=lang)
    ingredients = get_all_ingredients()
    ingredients_state = load_ingredients_state()
    
    # Create a mapping of English ingredient names to their translated display names
    ingredients_display = {}
    with open(COCKTAILS_FILE, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    all_cocktails = data.get('cocktails', [])
    for cocktail in all_cocktails:
        for ingredient in cocktail.get('ingredients', []):
            ingredient_name_en = get_ingredient_name_en(ingredient)
            if ingredient_name_en not in ingredients_display:
                ingredients_display[ingredient_name_en] = get_ingredient_name(ingredient, lang)
    
    return render_template('admin.html',
                         grouped_cocktails=grouped_cocktails,
                         ingredients=ingredients,
                         ingredients_display=ingredients_display,
                         ingredients_state=ingredients_state,
                         hidden_ingredients=HIDDEN_INGREDIENTS_ADMIN,
                         lang=lang)


@app.route('/api/state')
def get_state():
    """Get the current enabled/disabled state of all cocktails."""
    lang = session.get('lang', 'fr')
    cocktails = load_cocktails(lang)
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

        # Find cocktails that use this ingredient (using English name as key)
        cocktails_to_check = [c for c in cocktails if any(
            get_ingredient_name(ing, 'en') == ingredient_name for ing in c.get('ingredients', [])
        )]

        # For each cocktail using this ingredient, check if override should be cleared
        for cocktail in cocktails_to_check:
            cocktail_name = cocktail['name']
            # Only clear override if cocktail has an override
            if cocktail_name in cocktail_overrides:
                # Check if all ingredients are now available
                all_available = all(
                    ingredients_state.get(get_ingredient_name(ing, 'en'), True)
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


@app.route('/api/set-language', methods=['POST'])
def set_language():
    """Set the language preference in session."""
    data = request.get_json()
    lang = data.get('lang', 'en')
    
    if lang not in ['en', 'fr']:
        return jsonify({'error': 'Invalid language'}), 400
    
    session['lang'] = lang
    return jsonify({'success': True, 'lang': lang})


@app.route('/images/<path:filename>')
def serve_image(filename):
    """Serve images from the images directory."""
    images_dir = Path(__file__).parent / 'images'
    return send_from_directory(images_dir, filename)


@app.route('/api/cocktails/ordered')
def get_ordered_cocktails():
    """Get ordered list of enabled cocktail names (in display order)."""
    lang = session.get('lang', 'fr')
    cocktails = load_cocktails(lang)
    # Filter to show only enabled cocktails
    enabled_cocktails = [c for c in cocktails if c.get('enabled', True)]
    # Group cocktails by main alcohol (same logic as index page)
    grouped_cocktails = group_cocktails_by_alcohol(enabled_cocktails, lang=lang)
    # Flatten to get ordered list of names
    ordered_names = []
    for alcohol, cocktail_list in grouped_cocktails:
        ordered_names.extend([c['name'] for c in cocktail_list])
    return jsonify(ordered_names)


@app.route('/api/cocktail/<cocktail_name>')
def get_cocktail_detail(cocktail_name):
    """Get detailed information about a specific cocktail."""
    lang = session.get('lang', 'fr')
    cocktails = load_cocktails(lang)
    
    # Find the cocktail by name
    cocktail = next((c for c in cocktails if c['name'] == cocktail_name), None)
    
    if not cocktail:
        return jsonify({'error': 'Cocktail not found'}), 404
    
    # Process image path - if it's a relative path, make it absolute
    image_path = cocktail.get('image', '')
    if image_path and not image_path.startswith('http') and not image_path.startswith('/'):
        # It's a relative path, normalize it first
        # Remove ./ prefix if present
        if image_path.startswith('./'):
            image_path = image_path[2:]
        # Remove images/ prefix if present (we'll add /images/ ourselves)
        if image_path.startswith('images/'):
            image_path = image_path[7:]
        # Make it absolute
        image_path = f'/images/{image_path}'
    elif image_path and image_path.startswith('images/'):
        # Handle images/ prefix
        image_path = f'/{image_path}'
    
    # Return cocktail details with all ingredients (no filtering)
    return jsonify({
        'name': cocktail['name'],
        'image': image_path,
        'ingredients': [
            {
                'name': ingredient.get('display_name', ''),
                'qty': ingredient.get('qty', '')
            }
            for ingredient in cocktail.get('ingredients', [])
        ]
    })


if __name__ == '__main__':
    # Development server only - use gunicorn for production
    app.run(host='0.0.0.0', port=5000, debug=True)
