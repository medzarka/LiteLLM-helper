import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# Adjust imports for running inside the Flask app context
try:
    from models.models import AIModel, Database, Provider, APIKey
    from services.discovery import discover_free_models
except ImportError:
    from ..models.models import AIModel, Database, Provider, APIKey
    from .discovery import discover_free_models

def send_email(subject, html_content):
    smtp_server = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
    smtp_port = int(os.environ.get('SMTP_PORT', 587))
    smtp_username = os.environ.get('SMTP_USERNAME', '')
    smtp_password = os.environ.get('SMTP_PASSWORD', '')
    email_from = os.environ.get('EMAIL_FROM', smtp_username)
    email_to = os.environ.get('EMAIL_TO', smtp_username)

    if not smtp_username or not smtp_password:
        msg = "Notification skip: SMTP credentials not configured."
        print(msg)
        return {"success": False, "message": msg}

    msg = MIMEMultipart("alternative")
    msg['Subject'] = subject
    msg['From'] = email_from
    msg['To'] = email_to

    msg.attach(MIMEText(html_content, 'html'))

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.ehlo()
        server.starttls()
        server.login(smtp_username, smtp_password)
        server.sendmail(email_from, email_to, msg.as_string())
        server.quit()
        success_msg = f"Successfully sent email to {email_to}"
        print(success_msg)
        return {"success": True, "message": success_msg}
    except Exception as e:
        error_msg = f"Failed to send email: {e}"
        print(error_msg)
        return {"success": False, "message": error_msg}

def check_models_and_notify():
    print(f"Running check_models_and_notify at {datetime.now()}")
    
    try:
        db = Database()
        
        # 1. Fetch Discovered Models
        discovered_models = discover_free_models()
        discovered_dict = {m['id']: m for m in discovered_models}
        
        # 2. Fetch Local Models
        cursor = db.conn.cursor()
        cursor.execute('''
            SELECT m.id, m.actual_model, p.name 
            FROM model m 
            JOIN provider p ON m.provider_id = p.id
        ''')
        local_models = cursor.fetchall()
        
        # We need a list of existing actual_models
        local_actual_models = [row[1] for row in local_models]
        
        # Providers that support auto-discovery (from discovery.py logic)
        auto_discovery_providers = ['openrouter', 'gemini', 'google', 'mistral', 'groq', 'huggingface', 'github']
        
        # 3. Find New Models (in discovered_models but not in local_models)
        new_models = []
        for d_model in discovered_models:
            if d_model['id'] not in local_actual_models:
                new_models.append(d_model)
                
        # 4. Find Deprecated Models (in local_models from auto-discovery providers, but not in discovered_models)
        deprecated_models = []
        for l_model in local_models:
            model_id = l_model[0]
            actual_model = l_model[1]
            provider_name = l_model[2].lower()
            
            # If the provider is one we auto-discover from, and the actual_model is no longer in discovery list
            if provider_name in auto_discovery_providers and actual_model not in discovered_dict:
                deprecated_models.append({
                    'id': model_id,
                    'actual_model': actual_model,
                    'provider': provider_name
                })
                
        if not new_models and not deprecated_models:
            msg = "No new or deprecated models found."
            print(msg)
            return {"success": True, "message": msg}

        # 5. Format HTML Email
        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333;">
            <h2>LiteLLM Helper - Model Updates</h2>
            <p>Here is your bi-weekly update on discovered free AI models.</p>
        """

        if new_models:
            html += f"<h3 style='color: #2e7d32;'>New Models Found ({len(new_models)})</h3><ul>"
            for nm in new_models:
                desc = nm.get('description', '')
                if len(desc) > 100: desc = desc[:100] + "..."
                html += f"<li><strong>{nm.get('name', nm['id'])}</strong> ({nm.get('provider', 'Unknown')}) - <em>{nm['id']}</em><br><small>{desc}</small></li>"
            html += "</ul>"

        if deprecated_models:
            html += f"<h3 style='color: #c62828;'>Deprecated/Removed Models ({len(deprecated_models)})</h3>"
            html += "<p>These models were previously auto-discovered but are no longer available in the free tier feeds.</p><ul>"
            for dm in deprecated_models:
                html += f"<li><strong>{dm['actual_model']}</strong> (Provider: {dm['provider']})</li>"
            html += "</ul>"

        html += """
            <br>
            <p>You can add or remove these models from your LiteLLM Helper Dashboard.</p>
        </body>
        </html>
        """

        # 6. Send Email
        subject = f"LiteLLM Models Update: {len(new_models)} New, {len(deprecated_models)} Deprecated"
        return send_email(subject, html)
        
    except Exception as e:
        msg = f"Error in check_models_and_notify: {e}"
        print(msg)
        return {"success": False, "message": msg}

def check_usage_and_notify():
    print(f"Running check_usage_and_notify at {datetime.now()}")
    
    try:
        db = Database()
        providers = Provider(db).get_all()
        
        import redis
        import hashlib
        redis_host = os.environ.get('REDIS_HOST', 'litellm-redis')
        redis_port = int(os.environ.get('REDIS_PORT', 6379))
        redis_password = os.environ.get('REDIS_PASSWORD', '')
        
        r = None
        try:
            r = redis.Redis(host=redis_host, port=redis_port, password=redis_password, decode_responses=True)
            r.ping()
        except Exception:
            r = None
            
        if not r:
            msg = "Cannot connect to Redis, skipping usage notification."
            print(msg)
            return {"success": False, "message": msg}

        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333;">
            <h2>LiteLLM Helper - Daily API Usage</h2>
            <p>Here is your daily usage report for API keys grouped by provider.</p>
        """
        
        has_usage = False

        for provider in providers:
            keys = APIKey(db).get_by_provider(provider['id'])
            if not keys:
                continue
                
            provider_html = f"<h3 style='border-bottom: 1px solid #ccc; padding-bottom: 5px; color: #0284c7;'>{provider['name']}</h3>"
            provider_html += "<ul>"
            
            provider_has_usage = False
            for key in keys:
                if not key['is_active']:
                    continue
                    
                key_value = key.get('key_value', '')
                hashed_key = hashlib.sha256(key_value.encode()).hexdigest()
                
                try:
                    rpd_val = r.get(f"rpd:{hashed_key}") or r.get(f"rpd:{key_value}") or 0
                    current_rpd = int(rpd_val)
                    if current_rpd > 0:
                        provider_html += f"<li><strong>{key['key_name']}:</strong> {current_rpd} requests today</li>"
                        provider_has_usage = True
                        has_usage = True
                except Exception:
                    pass
            
            provider_html += "</ul>"
            if provider_has_usage:
                html += provider_html

        html += """
        </body>
        </html>
        """

        if has_usage:
            subject = f"LiteLLM Daily Usage Report - {datetime.now().strftime('%Y-%m-%d')}"
            return send_email(subject, html)
        else:
            msg = "No API usage recorded today."
            print(msg)
            return {"success": True, "message": msg}

    except Exception as e:
        msg = f"Error in check_usage_and_notify: {e}"
        print(msg)
        return {"success": False, "message": msg}
