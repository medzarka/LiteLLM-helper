from flask import Blueprint, request, jsonify
import imaplib
import email
import email.utils
from email.header import decode_header

try:
    from ..models.models import Database, EmailAccount, APIKey, Provider as DBProvider
except (ImportError, ValueError):
    from models.models import Database, EmailAccount, APIKey, Provider as DBProvider

bp = Blueprint('emails', __name__)
db = Database()

@bp.route('/emails', methods=['GET'])
def list_emails():
    emails = EmailAccount(db).get_all()
    return jsonify(emails)

@bp.route('/emails/force-notify', methods=['POST'])
def force_notify():
    try:
        from .notifications import check_models_and_notify, check_usage_and_notify
    except (ImportError, ValueError):
        from services.notifications import check_models_and_notify, check_usage_and_notify
        
    models_result = check_models_and_notify()
    usage_result = check_usage_and_notify()
    
    return jsonify({
        'models_notification': models_result,
        'usage_notification': usage_result
    }), 200

@bp.route('/emails/weekly-digest', methods=['POST'])
def trigger_weekly_digest():
    try:
        from .notifications import send_weekly_digest
    except (ImportError, ValueError):
        from services.notifications import send_weekly_digest
    res = send_weekly_digest(force=True)
    return jsonify(res), (200 if res.get('success') else 500)

@bp.route('/emails/weekly-digest/preview', methods=['GET'])
def preview_weekly_digest():
    try:
        from .notifications import build_weekly_digest_content
    except (ImportError, ValueError):
        from services.notifications import build_weekly_digest_content
    subject, html, metrics = build_weekly_digest_content()
    if request.args.get('format') == 'json':
        return jsonify({'subject': subject, 'metrics': metrics})
    return html, 200, {'Content-Type': 'text/html; charset=utf-8'}

@bp.route('/emails', methods=['POST'])
def create_email():
    try:
        data = request.get_json()
        email_type = data.get('email_type', 'other').lower()
        email = data['email'].lower()
        
        if email.endswith('@outlook.com') or email.endswith('@hotmail.com'):
            email_type = 'hotmail'
        elif '@yahoo.' in email:
            email_type = 'yahoo'
            
        email_service = EmailAccount(db)
        email_id = email_service.create(
            email=data['email'],
            password=data.get('password', ''),
            email_type=email_type
        )
        return jsonify({
            'id': email_id,
            'email': data['email'],
            'email_type': data.get('email_type', 'other'),
            'key_count': 0
        }), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@bp.route('/emails/<int:email_id>', methods=['PUT'])
def update_email(email_id):
    try:
        email_service = EmailAccount(db)
        existing = email_service.get_by_id(email_id)
        if not existing:
            return jsonify({'error': 'Not found'}), 404
        data = request.get_json()
        email = data.get('email', '')
        email_type = data.get('email_type')
        
        if email and (email.lower().endswith('@outlook.com') or email.lower().endswith('@hotmail.com')):
            email_type = 'hotmail'
        elif email and '@yahoo.' in email.lower():
            email_type = 'yahoo'
            
        email_service.update(
            email_id,
            email=email,
            password=data.get('password'),
            email_type=email_type
        )
        return jsonify(email_service.get_by_id(email_id)), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@bp.route('/emails/<int:email_id>', methods=['DELETE'])
def delete_email(email_id):
    try:
        if EmailAccount(db).delete(email_id):
            return '', 204
        return '', 404
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@bp.route('/emails/csv', methods=['POST'])
def import_csv():
    try:
        import csv
        import io
        if 'file' not in request.files:
            return jsonify({'error': 'No file part'}), 400
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No selected file'}), 400
        
        stream = io.StringIO(file.stream.read().decode("UTF8").strip(), newline=None)
        reader = csv.reader(stream)
        
        email_service = EmailAccount(db)
        imported = 0
        for i, row in enumerate(reader):
            if not row:
                continue
                
            email = row[0].strip()
            # Skip header row if present
            if i == 0 and email.lower() == 'email':
                continue
                
            if email:
                password = row[1].strip() if len(row) > 1 else ''
                email_type = row[2].strip().lower() if len(row) > 2 else 'other'
                
                if email.lower().endswith('@outlook.com') or email.lower().endswith('@hotmail.com'):
                    email_type = 'hotmail'
                elif '@yahoo.' in email.lower():
                    email_type = 'yahoo'
                    
                try:
                    cursor = db.conn.cursor()
                    cursor.execute('SELECT id FROM email_account WHERE email = ?', (email,))
                    existing = cursor.fetchone()
                    
                    if existing:
                        email_service.update(
                            email_id=existing[0],
                            password=password,
                            email_type=email_type
                        )
                    else:
                        email_service.create(
                            email=email,
                            password=password,
                            email_type=email_type
                        )
                    imported += 1
                except Exception:
                    pass
        return jsonify({'imported': imported}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@bp.route('/emails/<int:email_id>/keys', methods=['GET', 'POST'])
def handle_email_keys(email_id):
    if request.method == 'GET':
        try:
            cursor = db.conn.cursor()
            cursor.execute('''
                SELECT k.id, k.provider_id, k.key_name, k.key_value, k.is_active, k.created_at, p.name, p.provider_type 
                FROM api_key k
                JOIN provider p ON k.provider_id = p.id
                WHERE k.email_id = ?
            ''', (email_id,))
            keys = []
            for row in cursor.fetchall():
                keys.append({
                    'id': row[0],
                    'provider_id': row[1],
                    'key_name': row[2],
                    'key_value': row[3],
                    'is_active': row[4],
                    'created_at': row[5],
                    'provider_name': row[6],
                    'provider_type': row[7]
                })
            return jsonify(keys), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 400

    # POST method
    try:
        data = request.get_json()
        provider_name = data['provider_name']
        key_value = data['key_value']
        key_name = data.get('key_name', '')
        
        provider_obj = DBProvider(db).get_by_name(provider_name)
        if not provider_obj:
            return jsonify({'error': f'Provider {provider_name} not found'}), 404
        
        key_service = APIKey(db)
        key_id = key_service.create(
            provider_obj['id'],
            email_id,
            key_name,
            key_value,
            active=True
        )
        return jsonify({'id': key_id}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@bp.route('/emails/<int:email_id>/fetch_recent', methods=['GET'])
def fetch_recent_emails(email_id):
    try:
        email_service = EmailAccount(db)
        account = email_service.get_by_id(email_id)
        if not account:
            return jsonify({'error': 'Email account not found'}), 404
        
        email_type = account.get('email_type', 'other')
        user_email = account.get('email')
        password = account.get('password')
        
        if not password:
            return jsonify({'error': 'No password provided for this account'}), 400
            
        imap_servers = {
            'gmail': 'imap.gmail.com',
            'hotmail': 'outlook.office365.com',
            'outlook': 'outlook.office365.com',
            'gmx': 'imap.gmx.com',
            'mail': 'imap.mail.com',
            'yahoo': 'imap.mail.yahoo.com'
        }
        
        imap_server = imap_servers.get(email_type)
        if not imap_server:
            return jsonify({'error': f'Cannot fetch emails for type "{email_type}" (unknown IMAP server)'}), 400
            
        # Connect to IMAP
        try:
            mail = imaplib.IMAP4_SSL(imap_server)
            mail.login(user_email, password)
        except Exception as e:
            return jsonify({'error': f'IMAP login failed: {str(e)}'}), 401
            
        emails_list = []
        
        def fetch_from_folder(folder_name):
            try:
                print(f"DEBUG: Trying folder {folder_name}")
                status, _ = mail.select(folder_name)
                print(f"DEBUG: select {folder_name} status: {status}")
                if status != 'OK':
                    return []
                status, data = mail.search(None, 'ALL')
                print(f"DEBUG: search {folder_name} status: {status}, data[0] length: {len(data[0])}")
                if status != 'OK' or not data[0]:
                    return []
                
                mail_ids = data[0].split()
                print(f"DEBUG: Found {len(mail_ids)} emails in {folder_name}")
                recent_ids = mail_ids[-10:]
                
                fetched = []
                for i in reversed(recent_ids):
                    status, msg_data = mail.fetch(i, '(RFC822)')
                    if status != 'OK':
                        print(f"DEBUG: Failed to fetch email ID {i}")
                        continue
                        
                    for response_part in msg_data:
                        if isinstance(response_part, tuple):
                            msg = email.message_from_bytes(response_part[1])
                            
                            # Decode Subject
                            subject_header = msg.get('Subject', '')
                            subject_decoded, encoding = decode_header(subject_header)[0]
                            if isinstance(subject_decoded, bytes):
                                subject = subject_decoded.decode(encoding if encoding else 'utf-8', errors='ignore')
                            else:
                                subject = str(subject_decoded)
                                
                            # Decode From
                            from_header_raw = msg.get('From', '')
                            from_decoded, encoding = decode_header(from_header_raw)[0]
                            if isinstance(from_decoded, bytes):
                                from_header = from_decoded.decode(encoding if encoding else 'utf-8', errors='ignore')
                            else:
                                from_header = str(from_decoded)
                                
                            date_header = msg.get('Date', '')
                            
                            # Prefix subject with folder if not INBOX
                            prefix = '' if folder_name.upper() == 'INBOX' else f'[{folder_name}] '
                            
                            fetched.append({
                                'subject': prefix + subject,
                                'from': from_header,
                                'date': date_header,
                                '_raw_date': date_header
                            })
                return fetched
            except Exception as e:
                import traceback
                print(f"DEBUG: Exception in fetch_from_folder: {e}")
                traceback.print_exc()
                return []
                
        # Fetch INBOX
        emails_list.extend(fetch_from_folder('INBOX'))
        
        # Try common spam folders until one works
        spam_folders = ['"Spam"', 'Spam', '"[Gmail]/Spam"', 'Junk', '"Junk E-mail"', 'Bulk']
        for sf in spam_folders:
            spam_emails = fetch_from_folder(sf)
            if spam_emails:
                emails_list.extend(spam_emails)
                break
                
        # Sort combined by date
        def parse_date(d):
            try:
                return email.utils.parsedate_to_datetime(d).timestamp()
            except Exception:
                return 0
                
        emails_list.sort(key=lambda x: parse_date(x['_raw_date']), reverse=True)
        emails_list = emails_list[:10]
        
        # Clean up temp field
        for em in emails_list:
            em.pop('_raw_date', None)
                    
        mail.logout()
        return jsonify(emails_list), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
