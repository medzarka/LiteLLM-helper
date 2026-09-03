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


def build_weekly_digest_content():
    """
    Builds the subject and HTML content for the weekly digest report.
    Returns: (subject, html, metrics_dict)
    """
    now = datetime.now()
    try:
        from models.models import Database, DailyStats, ModelCatalogHistory
        from services.discovery import discover_free_models
    except ImportError:
        from ..models.models import Database, DailyStats, ModelCatalogHistory
        from .discovery import discover_free_models

    db = Database()

    # 1. Sync Model Discovery and Extract Changes (New & Deprecated)
    try:
        discovered = discover_free_models()
        model_changes = ModelCatalogHistory(db).sync_discovered_models(discovered)
    except Exception as e:
        print("Error during discover_free_models sync:", e)
        model_changes = {'new_models': [], 'deprecated_models': []}

    new_models = model_changes.get('new_models', [])
    deprecated_models = model_changes.get('deprecated_models', [])

    # 2. Retrieve 4-Week Usage Statistics & Top 10 Models
    usage_summary = DailyStats(db).get_four_week_summary()
    weeks = usage_summary['weeks']
    w1 = usage_summary['week1']
    w2 = usage_summary['week2']
    w3 = usage_summary['week3']
    w4 = usage_summary['week4']
    trend_pct = usage_summary['trend_pct']
    total_28d_requests = usage_summary['total_28d_requests']
    total_28d_tokens = usage_summary['total_28d_tokens']
    top_10_models = usage_summary.get('top_10_models_last_week', [])

    # 3. Generate Clean, Full-Information, Precise HTML Email
    root_domain = os.environ.get('ROOT_DOMAIN', 'bluewave.work')
    dashboard_url = f"https://litellm-helper.{root_domain}"

    trend_color = '#10b981' if trend_pct >= 0 else '#ef4444'
    trend_arrow = '▲' if trend_pct >= 0 else '▼'
    trend_sign = '+' if trend_pct >= 0 else ''

    # Build 4-Week Summary Table Rows
    weeks_rows_html = ""
    for wk in weeks:
        t_pct = wk.get('trend_pct', 0.0)
        t_col = '#10b981' if t_pct >= 0 else '#ef4444'
        t_sign = '+' if t_pct >= 0 else ''
        t_display = f"{t_sign}{t_pct}%" if wk['week_num'] < 4 else "-"
        is_current = wk['week_num'] == 1
        row_bg = '#f0fdf4' if is_current else '#ffffff'
        font_weight = '700' if is_current else '500'
        weeks_rows_html += f"""
        <tr style="border-bottom: 1px solid #e2e8f0; background: {row_bg};">
            <td style="padding: 9px 12px; font-weight: {font_weight}; color: #0f172a;">
                {wk['label']}
                <div style="font-weight: normal; color: #64748b; font-size: 11px;">{wk['start']} – {wk['end']}</div>
            </td>
            <td style="padding: 9px 12px; text-align: right; font-weight: {font_weight}; color: #0f172a; font-family: monospace;">{wk['total_requests']:,}</td>
            <td style="padding: 9px 12px; text-align: right; color: #64748b; font-family: monospace; font-size: 12px;">{wk['total_tokens']:,}</td>
            <td style="padding: 9px 12px; text-align: right; font-weight: 600; color: {t_col}; font-size: 12px;">{t_display}</td>
        </tr>
        """

    # Build Daily Comparison Table rows (matching Mon..Sun for Week 1 vs Week 2)
    daily_rows_html = ""
    w1_days = w1.get('days', [])
    w2_days = w2.get('days', [])

    for i in range(7):
        d1 = w1_days[i] if i < len(w1_days) else {'day_name': f"Day {i+1}", 'requests': 0, 'formatted': '-'}
        d2 = w2_days[i] if i < len(w2_days) else {'day_name': f"Day {i+1}", 'requests': 0, 'formatted': '-'}

        day_name = d1.get('day_name', f"Day {i+1}")
        r1 = d1.get('requests', 0)
        r2 = d2.get('requests', 0)

        if r2 > 0:
            d_delta = round(((r1 - r2) / r2) * 100)
            d_delta_str = f"{'+' if d_delta >= 0 else ''}{d_delta}%"
            d_delta_color = '#10b981' if d_delta >= 0 else '#64748b'
        elif r1 > 0:
            d_delta_str = "New"
            d_delta_color = '#10b981'
        else:
            d_delta_str = "0%"
            d_delta_color = '#94a3b8'

        daily_rows_html += f"""
        <tr style="border-bottom: 1px solid #e2e8f0;">
            <td style="padding: 8px 12px; font-weight: 600; color: #1e293b;">{day_name} <span style="font-weight: normal; color: #64748b; font-size: 11px;">({d1.get('formatted')})</span></td>
            <td style="padding: 8px 12px; text-align: right; color: #64748b; font-family: monospace;">{r2:,}</td>
            <td style="padding: 8px 12px; text-align: right; font-weight: 600; color: #0f172a; font-family: monospace;">{r1:,}</td>
            <td style="padding: 8px 12px; text-align: right; font-weight: 600; color: {d_delta_color}; font-size: 12px;">{d_delta_str}</td>
        </tr>
        """

    # Top 10 Models Used (Last Week) Section
    top_models_html = ""
    if top_10_models:
        top_models_html += """
        <div style="margin-top: 24px;">
            <h3 style="margin: 0 0 10px 0; font-size: 15px; color: #0f172a; font-weight: 700;">
                🔥 Top 10 Models Used (Last Week)
            </h3>
            <table style="width: 100%; border-collapse: collapse; font-size: 12px; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 6px;">
                <thead>
                    <tr style="background: #f8fafc; color: #475569; text-align: left; border-bottom: 2px solid #e2e8f0;">
                        <th style="padding: 8px 10px; width: 35px; text-align: center;">#</th>
                        <th style="padding: 8px 10px;">Model Name</th>
                        <th style="padding: 8px 10px;">Provider</th>
                        <th style="padding: 8px 10px; text-align: right;">Requests</th>
                        <th style="padding: 8px 10px; text-align: right;">Tokens</th>
                    </tr>
                </thead>
                <tbody>
        """
        for m in top_10_models:
            top_models_html += f"""
                    <tr style="border-bottom: 1px solid #f1f5f9;">
                        <td style="padding: 7px 10px; text-align: center; font-weight: 700; color: #64748b;">{m['rank']}</td>
                        <td style="padding: 7px 10px; font-weight: 600; color: #0f172a;">{m['model_name']}</td>
                        <td style="padding: 7px 10px; color: #0284c7; text-transform: uppercase; font-size: 11px; font-weight: 600;">{m.get('provider', 'unknown')}</td>
                        <td style="padding: 7px 10px; text-align: right; font-family: monospace; font-weight: 700; color: #0f172a;">{m['requests']:,}</td>
                        <td style="padding: 7px 10px; text-align: right; font-family: monospace; color: #64748b;">{m['tokens']:,}</td>
                    </tr>
            """
        top_models_html += "</tbody></table></div>"
    else:
        top_models_html = """
        <div style="margin-top: 20px; padding: 12px 14px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; font-size: 13px; color: #64748b;">
            <i style="color: #94a3b8;">No model requests recorded during the past 7 days.</i>
        </div>
        """

    # New Models Section
    new_models_html = ""
    if new_models:
        new_models_html += f"""
        <div style="margin-top: 24px;">
            <h3 style="margin: 0 0 10px 0; font-size: 15px; color: #0f766e; font-weight: 700;">
                ✨ New Free Models Discovered ({len(new_models)})
            </h3>
            <div style="overflow-x: auto;">
                <table style="width: 100%; border-collapse: collapse; font-size: 12px; background: #f0fdfa; border: 1px solid #ccfbf1; border-radius: 6px;">
                    <thead>
                        <tr style="background: #ccfbf1; color: #115e59; text-align: left;">
                            <th style="padding: 8px 10px;">Model Name</th>
                            <th style="padding: 8px 10px;">Provider</th>
                            <th style="padding: 8px 10px;">Context</th>
                            <th style="padding: 8px 10px;">Capabilities</th>
                        </tr>
                    </thead>
                    <tbody>
        """
        for nm in new_models[:12]:
            skills_str = ", ".join(nm.get('skills', [])) or ("Tools" if nm.get('supports_function_calling') else "Chat")
            ctx = f"{nm.get('context_length', 0):,} tokens" if nm.get('context_length') else "-"
            new_models_html += f"""
                        <tr style="border-bottom: 1px solid #e6fffa;">
                            <td style="padding: 7px 10px; font-weight: 600; color: #134e4a;">{nm.get('name', nm['id'])}</td>
                            <td style="padding: 7px 10px; color: #0f766e; text-transform: uppercase; font-size: 11px; font-weight: 600;">{nm.get('provider')}</td>
                            <td style="padding: 7px 10px; color: #334155; font-family: monospace;">{ctx}</td>
                            <td style="padding: 7px 10px; color: #0f766e; font-size: 11px;">{skills_str}</td>
                        </tr>
            """
        if len(new_models) > 12:
            new_models_html += f"""
                        <tr>
                            <td colspan="4" style="padding: 8px 10px; text-align: center; color: #0d9488; font-style: italic;">
                                + {len(new_models) - 12} additional models available in dashboard
                            </td>
                        </tr>
            """
        new_models_html += "</tbody></table></div></div>"
    else:
        new_models_html = """
        <div style="margin-top: 18px; padding: 10px 14px; background: #f8fafc; border-left: 3px solid #94a3b8; border-radius: 4px; font-size: 13px; color: #475569;">
            <strong>Catalog Status:</strong> No new models discovered this week. Upstream free tiers unchanged.
        </div>
        """

    # Deprecated Models Section (Always Visible)
    deprecated_html = ""
    if deprecated_models:
        deprecated_html += f"""
        <div style="margin-top: 24px;">
            <h3 style="margin: 0 0 10px 0; font-size: 15px; color: #b91c1c; font-weight: 700;">
                ⚠️ Deprecated / Removed Upstream Models ({len(deprecated_models)})
            </h3>
            <table style="width: 100%; border-collapse: collapse; font-size: 12px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 6px;">
                <thead>
                    <tr style="background: #fee2e2; color: #991b1b; text-align: left;">
                        <th style="padding: 8px 10px;">Model Name / ID</th>
                        <th style="padding: 8px 10px;">Provider</th>
                        <th style="padding: 8px 10px;">Status / Reason</th>
                    </tr>
                </thead>
                <tbody>
        """
        for dm in deprecated_models[:12]:
            dm_name = dm.get('name') or dm.get('id') or dm.get('model_id')
            dm_reason = dm.get('reason', 'Removed from upstream discovery feed')
            deprecated_html += f"""
                    <tr style="border-bottom: 1px solid #fee2e2;">
                        <td style="padding: 7px 10px; font-weight: 600; color: #7f1d1d;">{dm_name}</td>
                        <td style="padding: 7px 10px; color: #991b1b; text-transform: uppercase; font-size: 11px; font-weight: 600;">{dm.get('provider')}</td>
                        <td style="padding: 7px 10px; color: #b91c1c; font-size: 11px;">{dm_reason}</td>
                    </tr>
            """
        if len(deprecated_models) > 12:
            deprecated_html += f"""
                    <tr>
                        <td colspan="3" style="padding: 8px 10px; text-align: center; color: #991b1b; font-style: italic;">
                            + {len(deprecated_models) - 12} additional deprecated models
                        </td>
                    </tr>
            """
        deprecated_html += "</tbody></table></div>"
    else:
        deprecated_html = """
        <div style="margin-top: 20px; padding: 12px 14px; background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 6px; font-size: 13px; color: #166534;">
            <strong>✓ Upstream Health:</strong> 0 models deprecated this week. All provider free-tier models are stable.
        </div>
        """

    # HTML Email Document
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; background-color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
    <div style="max-width: 680px; margin: 25px auto; background: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 15px rgba(0,0,0,0.06); border: 1px solid #e2e8f0;">
        
        <!-- Header -->
        <div style="background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); padding: 26px 30px; color: #ffffff;">
            <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; color: #38bdf8; font-weight: 700; margin-bottom: 4px;">
                Homelab Sovereign AI Gateway
            </div>
            <h1 style="margin: 0; font-size: 22px; font-weight: 700; color: #ffffff;">
                Weekly Operations & Model Digest
            </h1>
            <div style="margin-top: 6px; font-size: 13px; color: #94a3b8;">
                4-Week Coverage: <strong>{w4.get('start')} – {w1.get('end')}, {now.year}</strong> (28 Days)
            </div>
        </div>

        <!-- Content Area -->
        <div style="padding: 26px 30px;">

            <!-- 4-Card KPI Overview Grid -->
            <table style="width: 100%; border-collapse: separate; border-spacing: 10px 0; margin-bottom: 24px;">
                <tr>
                    <td style="width: 25%; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px; text-align: center;">
                        <div style="font-size: 10px; font-weight: 600; text-transform: uppercase; color: #64748b;">Past 7 Days</div>
                        <div style="font-size: 20px; font-weight: 700; color: #0f172a; margin: 4px 0;">{w1.get('total_requests', 0):,}</div>
                        <div style="font-size: 11px; color: #0284c7; font-weight: 500;">Requests</div>
                    </td>
                    <td style="width: 25%; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px; text-align: center;">
                        <div style="font-size: 10px; font-weight: 600; text-transform: uppercase; color: #64748b;">4-Week Total</div>
                        <div style="font-size: 20px; font-weight: 700; color: #334155; margin: 4px 0;">{total_28d_requests:,}</div>
                        <div style="font-size: 11px; color: #64748b; font-weight: 500;">Requests</div>
                    </td>
                    <td style="width: 25%; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px; text-align: center;">
                        <div style="font-size: 10px; font-weight: 600; text-transform: uppercase; color: #64748b;">Weekly Trend</div>
                        <div style="font-size: 20px; font-weight: 700; color: {trend_color}; margin: 4px 0;">{trend_arrow} {trend_sign}{trend_pct}%</div>
                        <div style="font-size: 11px; color: #64748b; font-weight: 500;">WoW Delta</div>
                    </td>
                    <td style="width: 25%; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px; text-align: center;">
                        <div style="font-size: 10px; font-weight: 600; text-transform: uppercase; color: #64748b;">4-Week Tokens</div>
                        <div style="font-size: 20px; font-weight: 700; color: #0f172a; margin: 4px 0;">{total_28d_tokens:,}</div>
                        <div style="font-size: 11px; color: #0284c7; font-weight: 500;">Tokens</div>
                    </td>
                </tr>
            </table>

            <!-- 4-Week Summary Table -->
            <div style="margin-bottom: 24px;">
                <h3 style="margin: 0 0 10px 0; font-size: 15px; color: #0f172a; font-weight: 700;">
                    4-Week Request Volume Summary
                </h3>
                <table style="width: 100%; border-collapse: collapse; font-size: 13px; border: 1px solid #e2e8f0; border-radius: 6px; overflow: hidden;">
                    <thead>
                        <tr style="background: #f1f5f9; color: #475569; text-align: left; border-bottom: 2px solid #cbd5e1;">
                            <th style="padding: 9px 12px;">Week Period</th>
                            <th style="padding: 9px 12px; text-align: right;">Requests</th>
                            <th style="padding: 9px 12px; text-align: right;">Tokens</th>
                            <th style="padding: 9px 12px; text-align: right;">WoW Trend</th>
                        </tr>
                    </thead>
                    <tbody>
                        {weeks_rows_html}
                        <!-- Total Row -->
                        <tr style="background: #f8fafc; font-weight: 700; border-top: 2px solid #cbd5e1;">
                            <td style="padding: 10px 12px; color: #0f172a;">Total (All 4 Weeks)</td>
                            <td style="padding: 10px 12px; text-align: right; color: #0f172a; font-family: monospace;">{total_28d_requests:,}</td>
                            <td style="padding: 10px 12px; text-align: right; color: #64748b; font-family: monospace;">{total_28d_tokens:,}</td>
                            <td style="padding: 10px 12px; text-align: right; color: #64748b; font-size: 12px;">28 Days</td>
                        </tr>
                    </tbody>
                </table>
            </div>

            <!-- Daily Requests Comparison Table (Week 1 vs Week 2) -->
            <div style="margin-bottom: 24px;">
                <h3 style="margin: 0 0 10px 0; font-size: 15px; color: #0f172a; font-weight: 700;">
                    Daily Request Distribution (Past 7 Days vs Prior Week)
                </h3>
                <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
                    <thead>
                        <tr style="background: #f1f5f9; color: #475569; text-align: left; border-bottom: 2px solid #cbd5e1;">
                            <th style="padding: 8px 12px;">Day</th>
                            <th style="padding: 8px 12px; text-align: right;">Prior Week</th>
                            <th style="padding: 8px 12px; text-align: right;">Recent Week</th>
                            <th style="padding: 8px 12px; text-align: right;">Day Trend</th>
                        </tr>
                    </thead>
                    <tbody>
                        {daily_rows_html}
                    </tbody>
                </table>
            </div>

            {top_models_html}

            {new_models_html}

            {deprecated_html}

            <!-- CTA Button -->
            <div style="margin-top: 30px; text-align: center;">
                <a href="{dashboard_url}" style="display: inline-block; background: #0284c7; color: #ffffff; text-decoration: none; padding: 11px 24px; border-radius: 6px; font-weight: 600; font-size: 13px; box-shadow: 0 2px 5px rgba(2,132,199,0.3);">
                    Open LiteLLM Helper Dashboard &rarr;
                </a>
            </div>

        </div>

        <!-- Footer -->
        <div style="background: #f8fafc; padding: 18px 30px; border-top: 1px solid #e2e8f0; font-size: 12px; color: #64748b; text-align: center; line-height: 1.5;">
            Scheduled weekly operational digest • Sent every Monday at 06:00 AM<br>
            Managed by <strong>LiteLLM Helper v4</strong> on <em>oci01-flex.bluewave.work</em>
        </div>

    </div>
</body>
</html>
    """

    subject = f"LiteLLM Weekly Digest: {w1.get('total_requests', 0):,} Requests ({trend_sign}{trend_pct}%) | {len(new_models)} New Models"
    return subject, html, usage_summary


def send_weekly_digest(force=False):
    """
    Executes and sends the weekly operational digest.
    Guards with a distributed Redis lock to ensure only 1 worker process sends the email.
    """
    now = datetime.now()
    print(f"[{now}] Executing send_weekly_digest (force={force})...")

    if not force:
        import redis
        redis_host = os.environ.get('REDIS_HOST', 'litellm-redis')
        redis_port = int(os.environ.get('REDIS_PORT', 6379))
        redis_password = os.environ.get('REDIS_PASSWORD', '')
        try:
            r = redis.Redis(host=redis_host, port=redis_port, password=redis_password, decode_responses=True)
            # Acquire distributed lock for 10 minutes
            acquired = r.set('weekly_digest_lock', 'locked', nx=True, ex=600)
            if not acquired:
                msg = "Weekly digest already sent or running in another worker process, skipping."
                print(msg)
                return {"success": True, "message": msg}
        except Exception as e:
            print("Redis lock skipped:", e)

    try:
        subject, html, _ = build_weekly_digest_content()
        return send_email(subject, html)
    except Exception as e:
        import traceback
        traceback.print_exc()
        msg = f"Error in send_weekly_digest: {e}"
        print(msg)
        return {"success": False, "message": msg}

