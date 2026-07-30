import sys
import os

# Add the project root directory to Python path
# __file__ = /path/to/litellm_helper/v3/run.py
# dirname once = /path/to/litellm_helper/v3/
# dirname twice = /path/to/litellm_helper/
# dirname thrice = /path/to/litellm/  (project root)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

# Set Flask environment
os.environ['FLASK_ENV'] = os.environ.get('FLASK_ENV', 'production')

# Now we can import from litellm_helper.v3
try:
    from .app import create_app
except ImportError:
    from app import create_app

app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    print(f"Starting LiteLLM Helper v3 on port {port}...")
    print(f"Project root: {project_root}")
    app.run(debug=True, host='0.0.0.0', port=port)
