import os
import secrets
import logging
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template_string, request, redirect, session, jsonify, url_for
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import requests
from dotenv import load_dotenv
import string

# Charger les variables d'environnement
load_dotenv()

app = Flask(__name__)

# --- CONFIGURATION SÉCURISÉE ---
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-key-change-in-production-2024')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///wifi_flash.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Variables d'environnement
FEDAPAY_PUBLIC_KEY = os.getenv('FEDAPAY_PUBLIC_KEY', 'pk_live_oI1Z_UNXCvhmVAhfGdbwLFN3')
FEDAPAY_SECRET_KEY = os.getenv('FEDAPAY_SECRET_KEY', '')
MASTER_ADMIN_PASSWORD = os.getenv('MASTER_ADMIN_PASSWORD', 'MasterAdmin2024!')
PARRAINAGE_CODE = os.getenv('PARRAINAGE_CODE', 'SECRET_PARRAINAGE_2024')
COMMISSION_PERCENTAGE = 5  # 5% par défaut

# Initialiser la base de données
db = SQLAlchemy(app)

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- MODÈLES DE DONNÉES ---

class Forfait(db.Model):
    """Modèle pour les forfaits WiFi"""
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), unique=True, nullable=False)
    prix = db.Column(db.Integer, nullable=False)  # en FCFA
    duree_heures = db.Column(db.Integer, nullable=False)
    description = db.Column(db.String(255))
    actif = db.Column(db.Boolean, default=True)
    transactions = db.relationship('Transaction', backref='forfait', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'nom': self.nom,
            'prix': self.prix,
            'duree_heures': self.duree_heures,
            'description': self.description,
            'actif': self.actif
        }


class Parrain(db.Model):
    """Modèle pour les parrains/revendeurs"""
    id = db.Column(db.Integer, primary_key=True)
    code_parrainage = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    nom = db.Column(db.String(100), nullable=False)
    prenom = db.Column(db.String(100), nullable=False)
    telephone = db.Column(db.String(20), nullable=False)
    date_naissance = db.Column(db.Date)
    adresse = db.Column(db.String(255))
    ville = db.Column(db.String(100))
    pais = db.Column(db.String(100))
    numero_cni = db.Column(db.String(50))
    password_hash = db.Column(db.String(255), nullable=False)
    fedapay_public_key = db.Column(db.String(255))  # Clé API FedaPay du parrain
    fedapay_secret_key = db.Column(db.String(255))  # Clé secrète FedaPay du parrain
    statut = db.Column(db.String(20), default='attente')  # attente, actif, suspendu
    date_inscription = db.Column(db.DateTime, default=datetime.utcnow)
    dernier_connexion = db.Column(db.DateTime)
    total_ventes = db.Column(db.Integer, default=0)
    total_commissions = db.Column(db.Integer, default=0)
    transactions = db.relationship('Transaction', backref='parrain', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            'id': self.id,
            'code_parrainage': self.code_parrainage,
            'email': self.email,
            'nom': self.nom,
            'prenom': self.prenom,
            'telephone': self.telephone,
            'statut': self.statut,
            'date_inscription': self.date_inscription.strftime('%d/%m/%Y'),
            'total_ventes': self.total_ventes,
            'total_commissions': self.total_commissions
        }


class Transaction(db.Model):
    """Modèle pour les transactions de paiement"""
    id = db.Column(db.Integer, primary_key=True)
    forfait_id = db.Column(db.Integer, db.ForeignKey('forfait.id'), nullable=False)
    parrain_id = db.Column(db.Integer, db.ForeignKey('parrain.id'), nullable=True)  # NULL si vente directe
    client_telephone = db.Column(db.String(20), nullable=False)
    montant = db.Column(db.Integer, nullable=False)
    code_wifi = db.Column(db.String(16), unique=True, nullable=False)  # Code d'accès unique
    statut = db.Column(db.String(20), default='en_attente')  # en_attente, payé, expiré, annulé
    reference_fedapay = db.Column(db.String(100))
    commission = db.Column(db.Integer, default=0)  # 5% du montant si via parrain
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    date_expiration = db.Column(db.DateTime)
    date_paiement = db.Column(db.DateTime)

    def generer_code_wifi(self):
        """Génère un code WiFi unique (format: XXXX-XXXX-XXXX)"""
        characters = string.ascii_uppercase + string.digits
        code = ''.join(secrets.choice(characters) for _ in range(12))
        self.code_wifi = f"{code[:4]}-{code[4:8]}-{code[8:12]}"
        return self.code_wifi

    def to_dict(self):
        return {
            'id': self.id,
            'forfait': self.forfait.nom,
            'client_telephone': self.client_telephone,
            'montant': self.montant,
            'code_wifi': self.code_wifi,
            'statut': self.statut,
            'commission': self.commission,
            'date_creation': self.date_creation.strftime('%d/%m %H:%M'),
            'date_expiration': self.date_expiration.strftime('%d/%m %H:%M') if self.date_expiration else None
        }


class AdminMaster(db.Model):
    """Modèle pour l'administrateur principal"""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    derniere_connexion = db.Column(db.DateTime)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


# --- DÉCORATEURS ---

def login_required_parrain(f):
    """Décorateur pour vérifier la session parrain"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'parrain_id' not in session:
            return redirect('/parrain/login')
        return f(*args, **kwargs)
    return decorated_function


def login_required_master(f):
    """Décorateur pour vérifier la session master admin"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'master_admin_id' not in session:
            return redirect('/master_admin/login')
        return f(*args, **kwargs)
    return decorated_function


def generer_code_parrainage():
    """Génère un code de parrainage unique"""
    while True:
        code = secrets.token_hex(6).upper()
        if not Parrain.query.filter_by(code_parrainage=code).first():
            return code


# --- ROUTES PUBLIQUES ---

@app.route('/')
def index():
    """Page d'accueil avec les forfaits"""
    forfaits = Forfait.query.filter_by(actif=True).all()
    return render_template_string(HTML_ACCUEIL, forfaits=forfaits, public_key=FEDAPAY_PUBLIC_KEY)


@app.route('/api/status/<int:transaction_id>')
def status_transaction(transaction_id):
    """Vérifier le statut d'une transaction"""
    transaction = Transaction.query.get(transaction_id)
    if not transaction:
        return jsonify({'erreur': 'Transaction non trouvée'}), 404
    
    return jsonify({
        'id': transaction.id,
        'statut': transaction.statut,
        'code_wifi': transaction.code_wifi if transaction.statut == 'payé' else None,
        'message': 'Paiement confirmé !' if transaction.statut == 'payé' else 'En attente de paiement'
    })


# --- ROUTES PARRAINAGE (PROTÉGÉES) ---

@app.route('/parrainage/inscription', methods=['GET', 'POST'])
def parrainage_inscription():
    """Page d'inscription du parrain (protégée par mot de passe)"""
    if request.method == 'GET':
        # Afficher le formulaire de validation du code
        return render_template_string(HTML_PARRAINAGE_VERIF_CODE)
    
    # Vérification du code de parrainage
    code = request.form.get('code')
    if code != PARRAINAGE_CODE:
        return render_template_string(HTML_PARRAINAGE_VERIF_CODE, erreur="Code de parrainage invalide ❌")
    
    # Code correct, afficher le formulaire d'inscription
    return render_template_string(HTML_PARRAINAGE_FORMULAIRE, code_verified=True)


@app.route('/parrainage/register', methods=['POST'])
def parrainage_register():
    """Enregistrement d'un nouveau parrain"""
    try:
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        nom = request.form.get('nom')
        prenom = request.form.get('prenom')
        telephone = request.form.get('telephone')
        date_naissance = request.form.get('date_naissance')
        adresse = request.form.get('adresse')
        ville = request.form.get('ville')
        pais = request.form.get('pais')
        numero_cni = request.form.get('numero_cni')

        # Validations
        if Parrain.query.filter_by(email=email).first():
            return render_template_string(HTML_PARRAINAGE_FORMULAIRE, 
                                        erreur="Cet email existe déjà ❌", code_verified=True)
        
        if password != confirm_password:
            return render_template_string(HTML_PARRAINAGE_FORMULAIRE, 
                                        erreur="Les mots de passe ne correspondent pas ❌", code_verified=True)
        
        if len(password) < 8:
            return render_template_string(HTML_PARRAINAGE_FORMULAIRE, 
                                        erreur="Le mot de passe doit faire minimum 8 caractères ❌", code_verified=True)

        # Créer le parrain
        parrain = Parrain(
            code_parrainage=generer_code_parrainage(),
            email=email,
            nom=nom,
            prenom=prenom,
            telephone=telephone,
            date_naissance=datetime.strptime(date_naissance, '%Y-%m-%d').date() if date_naissance else None,
            adresse=adresse,
            ville=ville,
            pais=pais,
            numero_cni=numero_cni,
            statut='attente'  # En attente de validation du master admin
        )
        parrain.set_password(password)
        
        db.session.add(parrain)
        db.session.commit()
        
        logger.info(f"Nouveau parrain enregistré: {email} (en attente de validation)")
        
        return render_template_string(HTML_PARRAINAGE_SUCCES, 
                                     email=email, 
                                     code=parrain.code_parrainage)

    except Exception as e:
        logger.error(f"Erreur inscription parrain: {str(e)}")
        return render_template_string(HTML_PARRAINAGE_FORMULAIRE, 
                                    erreur=f"Erreur: {str(e)}", code_verified=True)


# --- ROUTES PARRAIN (ADMIN PERSONNEL) ---

@app.route('/parrain/login', methods=['GET', 'POST'])
def parrain_login():
    """Connexion parrain"""
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        parrain = Parrain.query.filter_by(email=email).first()
        
        if not parrain:
            return render_template_string(HTML_PARRAIN_LOGIN, erreur="Email ou mot de passe incorrect ❌")
        
        if not parrain.check_password(password):
            return render_template_string(HTML_PARRAIN_LOGIN, erreur="Email ou mot de passe incorrect ❌")
        
        if parrain.statut != 'actif':
            return render_template_string(HTML_PARRAIN_LOGIN, 
                                        erreur=f"Votre compte est en attente de validation ⏳")
        
        session['parrain_id'] = parrain.id
        parrain.dernier_connexion = datetime.utcnow()
        db.session.commit()
        
        logger.info(f"Parrain connecté: {email}")
        return redirect('/parrain/dashboard')
    
    return render_template_string(HTML_PARRAIN_LOGIN)


@app.route('/parrain/dashboard')
@login_required_parrain
def parrain_dashboard():
    """Dashboard personnel du parrain"""
    parrain = Parrain.query.get(session['parrain_id'])
    
    transactions = Transaction.query.filter_by(parrain_id=parrain.id).order_by(
        Transaction.date_creation.desc()
    ).all()
    
    stats = {
        'total_ventes': len([t for t in transactions if t.statut == 'payé']),
        'montant_total': sum(t.montant for t in transactions if t.statut == 'payé'),
        'commissions_totales': sum(t.commission for t in transactions if t.statut == 'payé'),
        'en_attente': len([t for t in transactions if t.statut == 'en_attente'])
    }
    
    return render_template_string(HTML_PARRAIN_DASHBOARD, 
                                 parrain=parrain, 
                                 stats=stats, 
                                 transactions=transactions)


@app.route('/parrain/settings', methods=['GET', 'POST'])
@login_required_parrain
def parrain_settings():
    """Paramètres du compte parrain"""
    parrain = Parrain.query.get(session['parrain_id'])
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'update_password':
            old_password = request.form.get('old_password')
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')
            
            if not parrain.check_password(old_password):
                message = "❌ Ancien mot de passe incorrect"
            elif new_password != confirm_password:
                message = "❌ Les nouveaux mots de passe ne correspondent pas"
            elif len(new_password) < 8:
                message = "❌ Le mot de passe doit faire minimum 8 caractères"
            else:
                parrain.set_password(new_password)
                db.session.commit()
                logger.info(f"Mot de passe parrain {parrain.email} mis à jour")
                message = "✅ Mot de passe mis à jour avec succès!"
        
        elif action == 'update_fedapay':
            fedapay_public = request.form.get('fedapay_public_key')
            fedapay_secret = request.form.get('fedapay_secret_key')
            
            if not fedapay_public.startswith('pk_'):
                message = "❌ Clé publique invalide (doit commencer par 'pk_')"
            else:
                parrain.fedapay_public_key = fedapay_public
                parrain.fedapay_secret_key = fedapay_secret
                db.session.commit()
                logger.info(f"Clés FedaPay parrain {parrain.email} mises à jour")
                message = "✅ Clés FedaPay mises à jour avec succès!"
        
        return render_template_string(HTML_PARRAIN_SETTINGS, parrain=parrain, message=message)
    
    return render_template_string(HTML_PARRAIN_SETTINGS, parrain=parrain)


@app.route('/parrain/logout')
def parrain_logout():
    """Déconnexion parrain"""
    session.clear()
    return redirect('/')


# --- ROUTES MASTER ADMIN (CACHÉES) ---

@app.route('/x7k3j9m2l8n1p5r/login', methods=['GET', 'POST'])
def master_admin_login():
    """Connexion master admin (URL cachée)"""
    if request.method == 'POST':
        password = request.form.get('password')
        
        if password != MASTER_ADMIN_PASSWORD:
            logger.warning("Tentative de connexion master admin échouée")
            return render_template_string(HTML_MASTER_LOGIN, erreur="Mot de passe incorrect ❌")
        
        admin = AdminMaster.query.first()
        if not admin:
            admin = AdminMaster(username='master')
            admin.set_password(MASTER_ADMIN_PASSWORD)
            db.session.add(admin)
            db.session.commit()
        
        session['master_admin_id'] = admin.id
        admin.derniere_connexion = datetime.utcnow()
        db.session.commit()
        
        logger.info("Master admin connecté")
        return redirect('/x7k3j9m2l8n1p5r/dashboard')
    
    return render_template_string(HTML_MASTER_LOGIN)


@app.route('/x7k3j9m2l8n1p5r/dashboard')
@login_required_master
def master_admin_dashboard():
    """Dashboard master admin"""
    total_parrains = Parrain.query.count()
    parrains_actifs = Parrain.query.filter_by(statut='actif').count()
    parrains_attente = Parrain.query.filter_by(statut='attente').count()
    
    total_ventes = db.session.query(db.func.sum(Transaction.montant)).filter_by(statut='payé').scalar() or 0
    total_commissions_versees = db.session.query(db.func.sum(Transaction.commission)).filter_by(statut='payé').scalar() or 0
    
    transactions = Transaction.query.order_by(Transaction.date_creation.desc()).limit(10).all()
    
    stats = {
        'total_parrains': total_parrains,
        'parrains_actifs': parrains_actifs,
        'parrains_attente': parrains_attente,
        'total_ventes': total_ventes,
        'total_commissions': total_commissions_versees
    }
    
    return render_template_string(HTML_MASTER_DASHBOARD, stats=stats, transactions=transactions)


@app.route('/x7k3j9m2l8n1p5r/parrains')
@login_required_master
def master_admin_parrains():
    """Gestion des parrains"""
    parrains = Parrain.query.all()
    return render_template_string(HTML_MASTER_PARRAINS, parrains=parrains)


@app.route('/x7k3j9m2l8n1p5r/parrain/<int:parrain_id>', methods=['GET', 'POST'])
@login_required_master
def master_admin_parrain_detail(parrain_id):
    """Détails d'un parrain"""
    parrain = Parrain.query.get(parrain_id)
    if not parrain:
        return "Parrain non trouvé", 404
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'activer':
            parrain.statut = 'actif'
            logger.info(f"Parrain {parrain.email} activé")
            message = "✅ Parrain activé"
        
        elif action == 'suspendre':
            parrain.statut = 'suspendu'
            logger.info(f"Parrain {parrain.email} suspendu")
            message = "⚠️ Parrain suspendu"
        
        db.session.commit()
        return render_template_string(HTML_MASTER_PARRAIN_DETAIL, parrain=parrain, message=message)
    
    transactions = Transaction.query.filter_by(parrain_id=parrain_id).all()
    stats = {
        'total_ventes': len([t for t in transactions if t.statut == 'payé']),
        'montant_total': sum(t.montant for t in transactions if t.statut == 'payé'),
        'commissions_totales': sum(t.commission for t in transactions if t.statut == 'payé')
    }
    
    return render_template_string(HTML_MASTER_PARRAIN_DETAIL, parrain=parrain, 
                                 transactions=transactions, stats=stats)


@app.route('/x7k3j9m2l8n1p5r/transactions')
@login_required_master
def master_admin_transactions():
    """Voir toutes les transactions"""
    transactions = Transaction.query.order_by(Transaction.date_creation.desc()).all()
    return render_template_string(HTML_MASTER_TRANSACTIONS, transactions=transactions)


@app.route('/x7k3j9m2l8n1p5r/logout')
def master_admin_logout():
    """Déconnexion master admin"""
    session.clear()
    return redirect('/')


# --- INITIALISATION BASE DE DONNÉES ---

def initialiser_base_donnees():
    """Crée les tables et ajoute les données par défaut"""
    with app.app_context():
        db.create_all()
        
        # Vérifier si les forfaits existent déjà
        if Forfait.query.count() == 0:
            forfaits_defaut = [
                Forfait(nom="Flash Test", prix=100, duree_heures=2, description="Test gratuit"),
                Forfait(nom="Flash Starter", prix=200, duree_heures=24, description="1 jour"),
                Forfait(nom="Flash Premium", prix=500, duree_heures=72, description="3 jours"),
                Forfait(nom="Flash Giga", prix=1000, duree_heures=168, description="7 jours"),
                Forfait(nom="Flash Business", prix=2500, duree_heures=720, description="1 mois")
            ]
            db.session.add_all(forfaits_defaut)
            db.session.commit()
            logger.info("Forfaits par défaut créés")


# --- TEMPLATES HTML ---

HTML_ACCUEIL = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Flash WiFi ⚡</title>
    <script src="https://cdn.fedapay.com/checkout.js?v=1.1.7"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            color: white;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        header {
            text-align: center;
            margin-bottom: 50px;
            padding: 30px 0;
        }
        header h1 {
            font-size: 3em;
            margin-bottom: 10px;
            color: #facc15;
            text-shadow: 0 0 10px rgba(250, 204, 21, 0.5);
        }
        header p { font-size: 1.1em; color: #cbd5e1; }
        
        .forfaits-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 40px;
        }
        
        .card {
            background: rgba(30, 41, 59, 0.8);
            border: 2px solid #334155;
            border-radius: 15px;
            padding: 25px;
            transition: all 0.3s ease;
            backdrop-filter: blur(10px);
        }
        
        .card:hover {
            border-color: #facc15;
            box-shadow: 0 0 20px rgba(250, 204, 21, 0.3);
            transform: translateY(-5px);
        }
        
        .card h3 {
            font-size: 1.5em;
            margin-bottom: 10px;
            color: #facc15;
        }
        
        .card p { color: #cbd5e1; margin-bottom: 15px; }
        
        .price {
            font-size: 2em;
            font-weight: bold;
            color: #facc15;
            margin: 15px 0;
        }
        
        .btn {
            background: #facc15;
            color: #0f172a;
            border: none;
            padding: 12px 25px;
            border-radius: 8px;
            font-weight: bold;
            cursor: pointer;
            width: 100%;
            transition: all 0.3s ease;
            font-size: 1em;
        }
        
        .btn:hover {
            background: #eab308;
            transform: scale(1.02);
        }
        
        footer {
            text-align: center;
            margin-top: 50px;
            padding-top: 30px;
            border-top: 1px solid #334155;
            color: #64748b;
        }
        
        .admin-link {
            color: #475569;
            text-decoration: none;
            font-size: 0.9em;
            transition: color 0.3s ease;
        }
        
        .admin-link:hover { color: #facc15; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>⚡ Flash WiFi</h1>
            <p>Connexion ultra-rapide au Bénin</p>
        </header>

        <div class="forfaits-grid">
            {% for forfait in forfaits %}
            <div class="card">
                <h3>{{ forfait.nom }}</h3>
                <p>{{ forfait.description }}</p>
                <div class="price">{{ forfait.prix }} FCFA</div>
                <p style="color: #94a3b8; font-size: 0.9em;">{{ forfait.duree_heures }}h d'accès</p>
                <button class="btn" id="pay-btn-{{ forfait.id }}">Acheter via MTN / Moov</button>
                <script>
                    document.getElementById('pay-btn-{{ forfait.id }}').addEventListener('click', function() {
                        FedaPay.init('#pay-btn-{{ forfait.id }}', {
                            public_key: '{{ public_key }}',
                            transaction: {
                                amount: {{ forfait.prix }},
                                description: 'Forfait {{ forfait.nom }} - Flash WiFi',
                                email: 'client@flashwifi.bj'
                            },
                            onComplete: function(response) {
                                alert('Paiement confirmé! Votre code WiFi: XXXX-XXXX-XXXX');
                            }
                        });
                    });
                </script>
            </div>
            {% endfor %}
        </div>

        <footer>
            <p>Service WiFi Flash - Bénin 🇧🇯</p>
            <a href="/parrain/login" class="admin-link">Espace Parrain</a>
        </footer>
    </div>
</body>
</html>
"""

HTML_PARRAINAGE_VERIF_CODE = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Devenir Parrain - Flash WiFi</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        .container {
            background: rgba(30, 41, 59, 0.9);
            border: 2px solid #334155;
            border-radius: 15px;
            padding: 40px;
            max-width: 500px;
            width: 100%;
        }
        h1 {
            color: #facc15;
            margin-bottom: 10px;
            font-size: 2em;
            text-align: center;
        }
        p {
            color: #cbd5e1;
            text-align: center;
            margin-bottom: 30px;
            font-size: 1.1em;
        }
        .form-group {
            margin-bottom: 20px;
        }
        label {
            display: block;
            color: #cbd5e1;
            margin-bottom: 8px;
            font-weight: 500;
        }
        input {
            width: 100%;
            padding: 12px;
            border: 1px solid #475569;
            border-radius: 8px;
            background: rgba(15, 23, 42, 0.5);
            color: white;
            font-size: 1em;
            transition: border-color 0.3s ease;
        }
        input:focus {
            outline: none;
            border-color: #facc15;
            box-shadow: 0 0 8px rgba(250, 204, 21, 0.2);
        }
        button {
            width: 100%;
            padding: 12px;
            background: #facc15;
            color: #0f172a;
            border: none;
            border-radius: 8px;
            font-weight: bold;
            cursor: pointer;
            font-size: 1em;
            transition: all 0.3s ease;
        }
        button:hover {
            background: #eab308;
            transform: scale(1.02);
        }
        .error {
            background: rgba(239, 68, 68, 0.1);
            border: 1px solid #ef4444;
            color: #fca5a5;
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 20px;
            text-align: center;
        }
        .back-link {
            text-align: center;
            margin-top: 20px;
        }
        .back-link a {
            color: #64748b;
            text-decoration: none;
            transition: color 0.3s ease;
        }
        .back-link a:hover { color: #facc15; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🤝 Devenir Parrain</h1>
        <p>Gagnez des commissions en vendant Flash WiFi</p>
        
        {% if erreur %}
        <div class="error">{{ erreur }}</div>
        {% endif %}
        
        <form method="POST">
            <div class="form-group">
                <label for="code">Code de Parrainage</label>
                <input type="password" id="code" name="code" placeholder="Entrez le code" required>
                <p style="font-size: 0.85em; color: #94a3b8; margin-top: 5px;">
                    Contactez l'administrateur pour recevoir le code
                </p>
            </div>
            <button type="submit">Vérifier</button>
        </form>
        
        <div class="back-link">
            <a href="/">← Retour à l'accueil</a>
        </div>
    </div>
</body>
</html>
"""

HTML_PARRAINAGE_FORMULAIRE = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Inscription Parrain - Flash WiFi</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            color: white;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 700px; margin: 0 auto; }
        header {
            text-align: center;
            margin-bottom: 40px;
            padding: 30px 0;
        }
        header h1 {
            color: #facc15;
            font-size: 2em;
            margin-bottom: 10px;
        }
        .form-container {
            background: rgba(30, 41, 59, 0.8);
            border: 1px solid #334155;
            border-radius: 15px;
            padding: 30px;
            backdrop-filter: blur(10px);
        }
        .form-group {
            margin-bottom: 15px;
        }
        label {
            display: block;
            color: #cbd5e1;
            margin-bottom: 8px;
            font-weight: 500;
        }
        input, textarea {
            width: 100%;
            padding: 10px;
            border: 1px solid #475569;
            border-radius: 8px;
            background: rgba(15, 23, 42, 0.5);
            color: white;
            font-size: 0.95em;
            transition: border-color 0.3s ease;
        }
        input:focus, textarea:focus {
            outline: none;
            border-color: #facc15;
            box-shadow: 0 0 8px rgba(250, 204, 21, 0.2);
        }
        .form-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 15px;
        }
        button {
            width: 100%;
            padding: 12px;
            background: #facc15;
            color: #0f172a;
            border: none;
            border-radius: 8px;
            font-weight: bold;
            cursor: pointer;
            font-size: 1em;
            transition: all 0.3s ease;
            margin-top: 20px;
        }
        button:hover {
            background: #eab308;
            transform: scale(1.02);
        }
        .error {
            background: rgba(239, 68, 68, 0.1);
            border: 1px solid #ef4444;
            color: #fca5a5;
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 20px;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>📝 Inscription Parrain</h1>
        </header>

        <div class="form-container">
            {% if erreur %}
            <div class="error">{{ erreur }}</div>
            {% endif %}

            <form method="POST" action="/parrainage/register">
                <div class="form-row">
                    <div class="form-group">
                        <label for="prenom">Prénom</label>
                        <input type="text" id="prenom" name="prenom" required>
                    </div>
                    <div class="form-group">
                        <label for="nom">Nom</label>
                        <input type="text" id="nom" name="nom" required>
                    </div>
                </div>

                <div class="form-group">
                    <label for="email">Email</label>
                    <input type="email" id="email" name="email" required>
                </div>

                <div class="form-row">
                    <div class="form-group">
                        <label for="telephone">Téléphone</label>
                        <input type="tel" id="telephone" name="telephone" required>
                    </div>
                    <div class="form-group">
                        <label for="date_naissance">Date de Naissance</label>
                        <input type="date" id="date_naissance" name="date_naissance">
                    </div>
                </div>

                <div class="form-group">
                    <label for="numero_cni">Numéro CNI</label>
                    <input type="text" id="numero_cni" name="numero_cni">
                </div>

                <div class="form-group">
                    <label for="adresse">Adresse</label>
                    <input type="text" id="adresse" name="adresse">
                </div>

                <div class="form-row">
                    <div class="form-group">
                        <label for="ville">Ville</label>
                        <input type="text" id="ville" name="ville">
                    </div>
                    <div class="form-group">
                        <label for="pais">Pays</label>
                        <input type="text" id="pais" name="pais" value="Bénin">
                    </div>
                </div>

                <div class="form-row">
                    <div class="form-group">
                        <label for="password">Mot de passe</label>
                        <input type="password" id="password" name="password" required>
                        <p style="font-size: 0.8em; color: #94a3b8; margin-top: 5px;">Min. 8 caractères</p>
                    </div>
                    <div class="form-group">
                        <label for="confirm_password">Confirmer</label>
                        <input type="password" id="confirm_password" name="confirm_password" required>
                    </div>
                </div>

                <button type="submit">S'inscrire</button>
            </form>
        </div>
    </div>
</body>
</html>
"""

HTML_PARRAINAGE_SUCCES = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Inscription Confirmée - Flash WiFi</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        .container {
            background: rgba(30, 41, 59, 0.9);
            border: 2px solid #4ade80;
            border-radius: 15px;
            padding: 40px;
            max-width: 500px;
            width: 100%;
            text-align: center;
        }
        h1 {
            color: #4ade80;
            margin-bottom: 20px;
            font-size: 2em;
        }
        p {
            color: #cbd5e1;
            margin-bottom: 15px;
            line-height: 1.6;
        }
        .code-box {
            background: rgba(74, 222, 128, 0.1);
            border: 2px solid #4ade80;
            border-radius: 10px;
            padding: 20px;
            margin: 20px 0;
        }
        .code-label {
            color: #94a3b8;
            font-size: 0.9em;
            margin-bottom: 10px;
        }
        .code {
            color: #4ade80;
            font-size: 1.3em;
            font-weight: bold;
            font-family: monospace;
            letter-spacing: 2px;
        }
        .info {
            background: rgba(96, 165, 250, 0.1);
            border: 1px solid #3b82f6;
            border-radius: 8px;
            padding: 15px;
            margin: 20px 0;
            font-size: 0.95em;
        }
        a {
            display: inline-block;
            margin-top: 20px;
            color: #facc15;
            text-decoration: none;
            transition: color 0.3s ease;
        }
        a:hover { color: #eab308; }
    </style>
</head>
<body>
    <div class="container">
        <h1>✅ Inscription Confirmée!</h1>
        <p>Bienvenue dans le programme de parrainage Flash WiFi</p>
        
        <div class="code-box">
            <div class="code-label">Votre code parrainage unique:</div>
            <div class="code">{{ code }}</div>
        </div>

        <div class="info">
            <strong>📋 Prochaines étapes:</strong><br><br>
            1. Votre compte est en attente de validation par l'administrateur<br>
            2. Vous recevrez un email de confirmation<br>
            3. Vous pourrez alors accéder à votre espace personnel<br>
            4. Configurez vos clés FedaPay pour commencer à vendre
        </div>

        <p style="color: #94a3b8; font-size: 0.9em;">
            Email: {{ email }}<br>
            Vérifiez votre boîte mail pour les instructions
        </p>

        <a href="/">← Retour à l'accueil</a>
    </div>
</body>
</html>
"""

HTML_PARRAIN_LOGIN = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Connexion Parrain - Flash WiFi</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        .container {
            background: rgba(30, 41, 59, 0.9);
            border: 2px solid #334155;
            border-radius: 15px;
            padding: 40px;
            max-width: 450px;
            width: 100%;
        }
        h1 {
            color: #facc15;
            margin-bottom: 10px;
            font-size: 1.8em;
            text-align: center;
        }
        p {
            color: #cbd5e1;
            text-align: center;
            margin-bottom: 30px;
        }
        .form-group {
            margin-bottom: 20px;
        }
        label {
            display: block;
            color: #cbd5e1;
            margin-bottom: 8px;
            font-weight: 500;
        }
        input {
            width: 100%;
            padding: 12px;
            border: 1px solid #475569;
            border-radius: 8px;
            background: rgba(15, 23, 42, 0.5);
            color: white;
            font-size: 1em;
            transition: border-color 0.3s ease;
        }
        input:focus {
            outline: none;
            border-color: #facc15;
            box-shadow: 0 0 8px rgba(250, 204, 21, 0.2);
        }
        button {
            width: 100%;
            padding: 12px;
            background: #facc15;
            color: #0f172a;
            border: none;
            border-radius: 8px;
            font-weight: bold;
            cursor: pointer;
            font-size: 1em;
            transition: all 0.3s ease;
        }
        button:hover {
            background: #eab308;
            transform: scale(1.02);
        }
        .error {
            background: rgba(239, 68, 68, 0.1);
            border: 1px solid #ef4444;
            color: #fca5a5;
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 20px;
            text-align: center;
        }
        .links {
            text-align: center;
            margin-top: 20px;
            padding-top: 20px;
            border-top: 1px solid #334155;
        }
        .links a {
            color: #64748b;
            text-decoration: none;
            margin: 0 10px;
            transition: color 0.3s ease;
        }
        .links a:hover { color: #facc15; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🔐 Parrain</h1>
        <p>Accédez à votre espace personnel</p>
        
        {% if erreur %}
        <div class="error">{{ erreur }}</div>
        {% endif %}
        
        <form method="POST">
            <div class="form-group">
                <label for="email">Email</label>
                <input type="email" id="email" name="email" required autofocus>
            </div>
            <div class="form-group">
                <label for="password">Mot de passe</label>
                <input type="password" id="password" name="password" required>
            </div>
            <button type="submit">Connexion</button>
        </form>
        
        <div class="links">
            <a href="/">Accueil</a>
            <a href="/parrainage/inscription">S'inscrire</a>
        </div>
    </div>
</body>
</html>
"""

HTML_PARRAIN_DASHBOARD = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard Parrain - Flash WiFi</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            color: white;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 30px;
            padding: 20px;
            background: rgba(30, 41, 59, 0.8);
            border-radius: 10px;
            border: 1px solid #334155;
        }
        header h1 { color: #facc15; font-size: 1.8em; }
        .header-info {
            display: flex;
            gap: 20px;
            align-items: center;
        }
        .user-info {
            text-align: right;
        }
        .user-info p { color: #cbd5e1; font-size: 0.9em; }
        .user-info .code {
            color: #4ade80;
            font-weight: bold;
            font-family: monospace;
            font-size: 1.1em;
        }
        .btn-group {
            display: flex;
            gap: 10px;
        }
        .btn {
            padding: 10px 20px;
            border-radius: 8px;
            text-decoration: none;
            font-weight: bold;
            transition: all 0.3s ease;
            cursor: pointer;
            border: none;
        }
        .btn-settings {
            background: #3b82f6;
            color: white;
        }
        .btn-settings:hover {
            background: #2563eb;
        }
        .btn-logout {
            background: #ef4444;
            color: white;
        }
        .btn-logout:hover {
            background: #dc2626;
        }
        
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        
        .stat-card {
            background: rgba(30, 41, 59, 0.8);
            border: 2px solid #334155;
            border-top: 4px solid #facc15;
            border-radius: 10px;
            padding: 20px;
        }
        
        .stat-card h3 {
            color: #cbd5e1;
            font-size: 0.9em;
            margin-bottom: 10px;
            text-transform: uppercase;
        }
        
        .stat-value {
            font-size: 2em;
            font-weight: bold;
            color: #facc15;
        }
        
        .transactions-section {
            background: rgba(30, 41, 59, 0.8);
            border: 1px solid #334155;
            border-radius: 10px;
            padding: 20px;
            overflow-x: auto;
        }
        
        .transactions-section h2 {
            color: #facc15;
            margin-bottom: 20px;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
        }
        
        th {
            background: rgba(51, 65, 85, 0.8);
            color: #facc15;
            padding: 12px;
            text-align: left;
            font-weight: 600;
            border-bottom: 2px solid #475569;
        }
        
        td {
            padding: 12px;
            border-bottom: 1px solid #334155;
            color: #cbd5e1;
        }
        
        tr:hover { background: rgba(51, 65, 85, 0.3); }
        
        .status {
            display: inline-block;
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 0.85em;
            font-weight: 600;
        }
        
        .status-paye {
            background: rgba(74, 222, 128, 0.2);
            color: #4ade80;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>👤 {{ parrain.prenom }} {{ parrain.nom }}</h1>
            </div>
            <div class="header-info">
                <div class="user-info">
                    <p>Code Parrainage:</p>
                    <div class="code">{{ parrain.code_parrainage }}</div>
                </div>
                <div class="btn-group">
                    <a href="/parrain/settings" class="btn btn-settings">⚙️ Paramètres</a>
                    <a href="/parrain/logout" class="btn btn-logout">Déconnexion</a>
                </div>
            </div>
        </header>

        <div class="stats-grid">
            <div class="stat-card">
                <h3>💰 Total Encaissé</h3>
                <div class="stat-value">{{ stats.montant_total }} F</div>
            </div>
            <div class="stat-card">
                <h3>📊 Ventes Payées</h3>
                <div class="stat-value">{{ stats.total_ventes }}</div>
            </div>
            <div class="stat-card">
                <h3>🎁 Commissions (5%)</h3>
                <div class="stat-value">{{ stats.commissions_totales }} F</div>
            </div>
            <div class="stat-card">
                <h3>⏳ En Attente</h3>
                <div class="stat-value">{{ stats.en_attente }}</div>
            </div>
        </div>

        <div class="transactions-section">
            <h2>📋 Mes Ventes</h2>
            <table>
                <thead>
                    <tr>
                        <th>Date</th>
                        <th>Forfait</th>
                        <th>Montant</th>
                        <th>Commission (5%)</th>
                        <th>Code WiFi</th>
                        <th>Statut</th>
                    </tr>
                </thead>
                <tbody>
                    {% for t in transactions %}
                    <tr>
                        <td>{{ t.date_creation.strftime('%d/%m %H:%M') }}</td>
                        <td>{{ t.forfait.nom }}</td>
                        <td>{{ t.montant }} FCFA</td>
                        <td><strong>{{ t.commission }} FCFA</strong></td>
                        <td><code>{{ t.code_wifi }}</code></td>
                        <td>
                            <span class="status status-{{ t.statut }}">
                                {{ t.statut.upper() }}
                            </span>
                        </td>
                    </tr>
                    {% else %}
                    <tr>
                        <td colspan="6" style="text-align: center; color: #64748b;">
                            Aucune vente pour le moment
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</body>
</html>
"""

HTML_PARRAIN_SETTINGS = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Paramètres - Parrain Flash WiFi</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            color: white;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 800px; margin: 0 auto; }
        header {
            margin-bottom: 30px;
            padding: 20px;
            background: rgba(30, 41, 59, 0.8);
            border-radius: 10px;
            border: 1px solid #334155;
        }
        header h1 { color: #facc15; margin-bottom: 10px; }
        .back-btn {
            color: #64748b;
            text-decoration: none;
            transition: color 0.3s ease;
        }
        .back-btn:hover { color: #facc15; }
        
        .message {
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 20px;
        }
        
        .message.success {
            background: rgba(74, 222, 128, 0.1);
            border: 1px solid #4ade80;
            color: #4ade80;
        }
        
        .settings-card {
            background: rgba(30, 41, 59, 0.8);
            border: 1px solid #334155;
            border-radius: 10px;
            padding: 25px;
            margin-bottom: 20px;
        }
        
        .settings-card h2 {
            color: #facc15;
            margin-bottom: 15px;
            border-bottom: 2px solid #334155;
            padding-bottom: 10px;
        }
        
        .form-group {
            margin-bottom: 15px;
        }
        
        label {
            display: block;
            color: #cbd5e1;
            margin-bottom: 8px;
            font-weight: 500;
        }
        
        input, textarea {
            width: 100%;
            padding: 10px;
            border: 1px solid #475569;
            border-radius: 8px;
            background: rgba(15, 23, 42, 0.5);
            color: white;
            font-size: 0.95em;
            transition: border-color 0.3s ease;
        }
        
        input:focus, textarea:focus {
            outline: none;
            border-color: #facc15;
            box-shadow: 0 0 8px rgba(250, 204, 21, 0.2);
        }
        
        button {
            background: #facc15;
            color: #0f172a;
            border: none;
            padding: 10px 20px;
            border-radius: 8px;
            font-weight: bold;
            cursor: pointer;
            transition: all 0.3s ease;
        }
        
        button:hover {
            background: #eab308;
            transform: scale(1.02);
        }
        
        .hint {
            font-size: 0.85em;
            color: #94a3b8;
            margin-top: 5px;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>⚙️ Mes Paramètres</h1>
            <a href="/parrain/dashboard" class="back-btn">← Retour au Dashboard</a>
        </header>

        {% if message %}
        <div class="message success">{{ message }}</div>
        {% endif %}

        <!-- Changement de mot de passe personnel -->
        <div class="settings-card">
            <h2>🔒 Changer Mon Mot de Passe</h2>
            <form method="POST">
                <div class="form-group">
                    <label for="old_password">Ancien mot de passe</label>
                    <input type="password" id="old_password" name="old_password" required>
                </div>
                <div class="form-group">
                    <label for="new_password">Nouveau mot de passe</label>
                    <input type="password" id="new_password" name="new_password" required>
                    <div class="hint">Minimum 8 caractères</div>
                </div>
                <div class="form-group">
                    <label for="confirm_password">Confirmer</label>
                    <input type="password" id="confirm_password" name="confirm_password" required>
                </div>
                <input type="hidden" name="action" value="update_password">
                <button type="submit">Changer le mot de passe</button>
            </form>
        </div>

        <!-- Configuration FedaPay -->
        <div class="settings-card">
            <h2>💳 Mes Clés FedaPay</h2>
            <p style="color: #cbd5e1; margin-bottom: 15px;">
                Configurez vos clés FedaPay pour recevoir vos paiements
            </p>
            <form method="POST">
                <div class="form-group">
                    <label for="fedapay_public_key">Clé Publique FedaPay</label>
                    <input type="text" id="fedapay_public_key" name="fedapay_public_key" 
                           value="{{ parrain.fedapay_public_key or '' }}"
                           placeholder="pk_live_...">
                    <div class="hint">Votre clé doit commencer par "pk_"</div>
                </div>
                <div class="form-group">
                    <label for="fedapay_secret_key">Clé Secrète FedaPay</label>
                    <input type="password" id="fedapay_secret_key" name="fedapay_secret_key"
                           value="{{ parrain.fedapay_secret_key or '' }}"
                           placeholder="sk_live_...">
                    <div class="hint">Gardez-la secrète!</div>
                </div>
                <input type="hidden" name="action" value="update_fedapay">
                <button type="submit">Mettre à jour les clés</button>
            </form>
        </div>

        <!-- Mes informations -->
        <div class="settings-card">
            <h2>👤 Mes Informations</h2>
            <p style="color: #cbd5e1;">
                <strong>Nom:</strong> {{ parrain.nom }} {{ parrain.prenom }}<br>
                <strong>Email:</strong> {{ parrain.email }}<br>
                <strong>Téléphone:</strong> {{ parrain.telephone }}<br>
                <strong>Code Parrainage:</strong> <span style="color: #4ade80; font-family: monospace;">{{ parrain.code_parrainage }}</span><br>
                <strong>Statut:</strong> 
                <span style="color: {% if parrain.statut == 'actif' %}#4ade80{% else %}#fb923c{% endif %};">
                    {{ parrain.statut.upper() }}
                </span><br>
                <strong>Depuis:</strong> {{ parrain.date_inscription.strftime('%d/%m/%Y') }}
            </p>
        </div>
    </div>
</body>
</html>
"""

HTML_MASTER_LOGIN = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Master - Flash WiFi</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        .container {
            background: rgba(30, 41, 59, 0.9);
            border: 2px solid #ef4444;
            border-radius: 15px;
            padding: 40px;
            max-width: 450px;
            width: 100%;
        }
        h1 {
            color: #ef4444;
            margin-bottom: 10px;
            font-size: 1.8em;
            text-align: center;
        }
        p {
            color: #cbd5e1;
            text-align: center;
            margin-bottom: 30px;
            font-size: 0.95em;
        }
        .form-group {
            margin-bottom: 20px;
        }
        label {
            display: block;
            color: #cbd5e1;
            margin-bottom: 8px;
            font-weight: 500;
        }
        input {
            width: 100%;
            padding: 12px;
            border: 1px solid #475569;
            border-radius: 8px;
            background: rgba(15, 23, 42, 0.5);
            color: white;
            font-size: 1em;
            transition: border-color 0.3s ease;
        }
        input:focus {
            outline: none;
            border-color: #ef4444;
            box-shadow: 0 0 8px rgba(239, 68, 68, 0.2);
        }
        button {
            width: 100%;
            padding: 12px;
            background: #ef4444;
            color: white;
            border: none;
            border-radius: 8px;
            font-weight: bold;
            cursor: pointer;
            font-size: 1em;
            transition: all 0.3s ease;
        }
        button:hover {
            background: #dc2626;
            transform: scale(1.02);
        }
        .error {
            background: rgba(239, 68, 68, 0.1);
            border: 1px solid #ef4444;
            color: #fca5a5;
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 20px;
            text-align: center;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🔐 ADMIN MASTER</h1>
        <p>Zone réservée - Accès restreint</p>
        
        {% if erreur %}
        <div class="error">{{ erreur }}</div>
        {% endif %}
        
        <form method="POST">
            <div class="form-group">
                <label for="password">Mot de passe Master</label>
                <input type="password" id="password" name="password" required autofocus placeholder="••••••••">
            </div>
            <button type="submit">Accéder</button>
        </form>
    </div>
</body>
</html>
"""

HTML_MASTER_DASHBOARD = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard Master - Flash WiFi</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            color: white;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 30px;
            padding: 20px;
            background: rgba(30, 41, 59, 0.8);
            border-radius: 10px;
            border: 2px solid #ef4444;
        }
        header h1 { color: #ef4444; font-size: 2em; }
        .logout-btn {
            background: #ef4444;
            color: white;
            padding: 10px 20px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            text-decoration: none;
            transition: all 0.3s ease;
        }
        .logout-btn:hover { background: #dc2626; }
        
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        
        .stat-card {
            background: rgba(30, 41, 59, 0.8);
            border: 2px solid #334155;
            border-top: 4px solid #ef4444;
            border-radius: 10px;
            padding: 20px;
        }
        
        .stat-card h3 {
            color: #cbd5e1;
            font-size: 0.9em;
            margin-bottom: 10px;
            text-transform: uppercase;
        }
        
        .stat-value {
            font-size: 2.5em;
            font-weight: bold;
            color: #ef4444;
        }
        
        .nav-buttons {
            display: flex;
            gap: 10px;
            margin-bottom: 30px;
        }
        
        .nav-btn {
            background: #3b82f6;
            color: white;
            padding: 12px 25px;
            border: none;
            border-radius: 8px;
            text-decoration: none;
            cursor: pointer;
            font-weight: bold;
            transition: all 0.3s ease;
        }
        
        .nav-btn:hover {
            background: #2563eb;
            transform: scale(1.05);
        }
        
        .transactions-section {
            background: rgba(30, 41, 59, 0.8);
            border: 1px solid #334155;
            border-radius: 10px;
            padding: 20px;
            overflow-x: auto;
        }
        
        .transactions-section h2 {
            color: #ef4444;
            margin-bottom: 20px;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
        }
        
        th {
            background: rgba(51, 65, 85, 0.8);
            color: #ef4444;
            padding: 12px;
            text-align: left;
            font-weight: 600;
            border-bottom: 2px solid #475569;
        }
        
        td {
            padding: 12px;
            border-bottom: 1px solid #334155;
            color: #cbd5e1;
        }
        
        tr:hover { background: rgba(51, 65, 85, 0.3); }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>🔴 MASTER ADMIN PANEL</h1>
            </div>
            <a href="/x7k3j9m2l8n1p5r/logout" class="logout-btn">Déconnexion</a>
        </header>

        <div class="nav-buttons">
            <a href="/x7k3j9m2l8n1p5r/parrains" class="nav-btn">👥 Gérer Parrains</a>
            <a href="/x7k3j9m2l8n1p5r/transactions" class="nav-btn">💰 Transactions</a>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <h3>👥 Total Parrains</h3>
                <div class="stat-value">{{ stats.total_parrains }}</div>
            </div>
            <div class="stat-card">
                <h3>✅ Parrains Actifs</h3>
                <div class="stat-value">{{ stats.parrains_actifs }}</div>
            </div>
            <div class="stat-card">
                <h3>⏳ En Attente</h3>
                <div class="stat-value">{{ stats.parrains_attente }}</div>
            </div>
            <div class="stat-card">
                <h3>💰 Total Ventes</h3>
                <div class="stat-value">{{ stats.total_ventes }} F</div>
            </div>
            <div class="stat-card">
                <h3>💸 Commissions (5%)</h3>
                <div class="stat-value">{{ stats.total_commissions }} F</div>
            </div>
        </div>

        <div class="transactions-section">
            <h2>📋 Dernières Transactions</h2>
            <table>
                <thead>
                    <tr>
                        <th>Date</th>
                        <th>Parrain</th>
                        <th>Montant</th>
                        <th>Commission (5%)</th>
                        <th>Code WiFi</th>
                        <th>Statut</th>
                    </tr>
                </thead>
                <tbody>
                    {% for t in transactions %}
                    <tr>
                        <td>{{ t.date_creation.strftime('%d/%m %H:%M') }}</td>
                        <td>{{ t.parrain.prenom if t.parrain else 'Vente directe' }}</td>
                        <td>{{ t.montant }} FCFA</td>
                        <td>{{ t.commission }} FCFA</td>
                        <td><code>{{ t.code_wifi }}</code></td>
                        <td>{{ t.statut.upper() }}</td>
                    </tr>
                    {% else %}
                    <tr>
                        <td colspan="6" style="text-align: center; color: #64748b;">
                            Aucune transaction
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</body>
</html>
"""

HTML_MASTER_PARRAINS = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gestion Parrains - Flash WiFi Master</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            color: white;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        header {
            margin-bottom: 30px;
            padding: 20px;
            background: rgba(30, 41, 59, 0.8);
            border-radius: 10px;
            border: 1px solid #334155;
        }
        header h1 { color: #ef4444; margin-bottom: 10px; }
        .back-btn {
            color: #64748b;
            text-decoration: none;
            transition: color 0.3s ease;
        }
        .back-btn:hover { color: #ef4444; }
        
        .parrains-grid {
            display: grid;
            gap: 20px;
        }
        
        .parrain-card {
            background: rgba(30, 41, 59, 0.8);
            border: 2px solid #334155;
            border-radius: 10px;
            padding: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            cursor: pointer;
            transition: all 0.3s ease;
        }
        
        .parrain-card:hover {
            border-color: #ef4444;
            box-shadow: 0 0 20px rgba(239, 68, 68, 0.2);
        }
        
        .parrain-info {
            flex: 1;
        }
        
        .parrain-info h3 {
            color: #facc15;
            margin-bottom: 8px;
        }
        
        .parrain-info p {
            color: #cbd5e1;
            font-size: 0.9em;
            margin-bottom: 5px;
        }
        
        .status-badge {
            display: inline-block;
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 0.85em;
            font-weight: 600;
            margin-top: 10px;
        }
        
        .status-actif {
            background: rgba(74, 222, 128, 0.2);
            color: #4ade80;
        }
        
        .status-attente {
            background: rgba(249, 115, 22, 0.2);
            color: #fb923c;
        }
        
        .parrain-stats {
            display: flex;
            gap: 20px;
            margin-left: 20px;
            text-align: center;
        }
        
        .stat {
            min-width: 100px;
        }
        
        .stat p {
            font-size: 0.85em;
            color: #94a3b8;
        }
        
        .stat-value {
            font-size: 1.3em;
            font-weight: bold;
            color: #facc15;
        }
        
        .detail-btn {
            background: #3b82f6;
            color: white;
            padding: 8px 15px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            text-decoration: none;
            font-weight: bold;
            margin-left: 10px;
            transition: all 0.3s ease;
        }
        
        .detail-btn:hover {
            background: #2563eb;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>👥 Gestion des Parrains</h1>
            <a href="/x7k3j9m2l8n1p5r/dashboard" class="back-btn">← Retour Dashboard</a>
        </header>

        <div class="parrains-grid">
            {% for parrain in parrains %}
            <div class="parrain-card">
                <div class="parrain-info">
                    <h3>{{ parrain.prenom }} {{ parrain.nom }}</h3>
                    <p>📧 {{ parrain.email }}</p>
                    <p>📱 {{ parrain.telephone }}</p>
                    <p>🔑 {{ parrain.code_parrainage }}</p>
                    <span class="status-badge status-{{ parrain.statut }}">
                        {{ parrain.statut.upper() }}
                    </span>
                </div>
                <div class="parrain-stats">
                    <div class="stat">
                        <p>Ventes</p>
                        <div class="stat-value">{{ parrain.total_ventes }}</div>
                    </div>
                    <div class="stat">
                        <p>Commissions</p>
                        <div class="stat-value">{{ parrain.total_commissions }} F</div>
                    </div>
                </div>
                <a href="/x7k3j9m2l8n1p5r/parrain/{{ parrain.id }}" class="detail-btn">Détails</a>
            </div>
            {% else %}
            <p style="text-align: center; color: #64748b;">Aucun parrain</p>
            {% endfor %}
        </div>
    </div>
</body>
</html>
"""

HTML_MASTER_PARRAIN_DETAIL = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Détails Parrain - Flash WiFi Master</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            color: white;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1000px; margin: 0 auto; }
        header {
            margin-bottom: 30px;
            padding: 20px;
            background: rgba(30, 41, 59, 0.8);
            border-radius: 10px;
            border: 1px solid #334155;
        }
        header h1 { color: #ef4444; margin-bottom: 10px; }
        .back-btn {
            color: #64748b;
            text-decoration: none;
            transition: color 0.3s ease;
        }
        .back-btn:hover { color: #ef4444; }
        
        .message {
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 20px;
            background: rgba(74, 222, 128, 0.1);
            border: 1px solid #4ade80;
            color: #4ade80;
        }
        
        .info-card {
            background: rgba(30, 41, 59, 0.8);
            border: 1px solid #334155;
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 20px;
        }
        
        .info-card h2 {
            color: #facc15;
            margin-bottom: 15px;
        }
        
        .info-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 15px;
        }
        
        .info-row p {
            color: #cbd5e1;
            margin-bottom: 10px;
        }
        
        .info-row strong {
            color: #94a3b8;
        }
        
        .action-btn {
            padding: 10px 20px;
            margin-right: 10px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: bold;
            transition: all 0.3s ease;
        }
        
        .btn-activer {
            background: #4ade80;
            color: #0f172a;
        }
        
        .btn-activer:hover {
            background: #22c55e;
        }
        
        .btn-suspendre {
            background: #ef4444;
            color: white;
        }
        
        .btn-suspendre:hover {
            background: #dc2626;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>📋 {{ parrain.prenom }} {{ parrain.nom }}</h1>
            <a href="/x7k3j9m2l8n1p5r/parrains" class="back-btn">← Retour Parrains</a>
        </header>

        {% if message %}
        <div class="message">{{ message }}</div>
        {% endif %}

        <!-- Informations personnelles -->
        <div class="info-card">
            <h2>👤 Informations Personnelles</h2>
            <div class="info-grid">
                <div class="info-row">
                    <p><strong>Email:</strong> {{ parrain.email }}</p>
                    <p><strong>Téléphone:</strong> {{ parrain.telephone }}</p>
                    <p><strong>Date Naissance:</strong> {{ parrain.date_naissance if parrain.date_naissance else 'N/A' }}</p>
                </div>
                <div class="info-row">
                    <p><strong>CNI:</strong> {{ parrain.numero_cni if parrain.numero_cni else 'N/A' }}</p>
                    <p><strong>Adresse:</strong> {{ parrain.adresse if parrain.adresse else 'N/A' }}</p>
                    <p><strong>Ville:</strong> {{ parrain.ville if parrain.ville else 'N/A' }} - {{ parrain.pais if parrain.pais else 'N/A' }}</p>
                </div>
            </div>
        </div>

        <!-- Statut -->
        <div class="info-card">
            <h2>🔐 Gestion Compte</h2>
            <p style="color: #cbd5e1; margin-bottom: 15px;">
                <strong>Statut Actuel:</strong> 
                <span style="color: {% if parrain.statut == 'actif' %}#4ade80{% else %}#fb923c{% endif %};">
                    {{ parrain.statut.upper() }}
                </span>
            </p>
            {% if parrain.statut != 'actif' %}
            <form method="POST" style="display: inline;">
                <input type="hidden" name="action" value="activer">
                <button type="submit" class="action-btn btn-activer">✅ Activer le Parrain</button>
            </form>
            {% endif %}
            {% if parrain.statut != 'suspendu' %}
            <form method="POST" style="display: inline;">
                <input type="hidden" name="action" value="suspendre">
                <button type="submit" class="action-btn btn-suspendre">⛔ Suspendre</button>
            </form>
            {% endif %}
        </div>

        <!-- Statistiques -->
        <div class="info-card">
            <h2>📊 Statistiques</h2>
            <div class="info-grid">
                <div class="info-row">
                    <p><strong>Total Ventes:</strong> {{ stats.total_ventes }}</p>
                    <p><strong>Montant Total:</strong> {{ stats.montant_total }} FCFA</p>
                </div>
                <div class="info-row">
                    <p><strong>Commissions (5%):</strong> {{ stats.commissions_totales }} FCFA</p>
                    <p><strong>Depuis:</strong> {{ parrain.date_inscription.strftime('%d/%m/%Y') }}</p>
                </div>
            </div>
        </div>

        <!-- Transactions -->
        <div class="info-card">
            <h2>💰 Transactions</h2>
            <table style="width: 100%; border-collapse: collapse;">
                <thead>
                    <tr style="background: rgba(51, 65, 85, 0.8); border-bottom: 2px solid #475569;">
                        <th style="padding: 10px; text-align: left; color: #facc15;">Date</th>
                        <th style="padding: 10px; text-align: left; color: #facc15;">Forfait</th>
                        <th style="padding: 10px; text-align: left; color: #facc15;">Montant</th>
                        <th style="padding: 10px; text-align: left; color: #facc15;">Commission</th>
                        <th style="padding: 10px; text-align: left; color: #facc15;">Code WiFi</th>
                    </tr>
                </thead>
                <tbody>
                    {% for t in transactions %}
                    <tr style="border-bottom: 1px solid #334155;">
                        <td style="padding: 10px; color: #cbd5e1;">{{ t.date_creation.strftime('%d/%m %H:%M') }}</td>
                        <td style="padding: 10px; color: #cbd5e1;">{{ t.forfait.nom }}</td>
                        <td style="padding: 10px; color: #cbd5e1;">{{ t.montant }} F</td>
                        <td style="padding: 10px; color: #4ade80;"><strong>{{ t.commission }} F</strong></td>
                        <td style="padding: 10px; color: #cbd5e1;"><code>{{ t.code_wifi }}</code></td>
                    </tr>
                    {% else %}
                    <tr>
                        <td colspan="5" style="padding: 10px; text-align: center; color: #64748b;">
                            Aucune transaction
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</body>
</html>
"""

HTML_MASTER_TRANSACTIONS = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Transactions - Flash WiFi Master</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            color: white;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        header {
            margin-bottom: 30px;
            padding: 20px;
            background: rgba(30, 41, 59, 0.8);
            border-radius: 10px;
            border: 1px solid #334155;
        }
        header h1 { color: #ef4444; margin-bottom: 10px; }
        .back-btn {
            color: #64748b;
            text-decoration: none;
            transition: color 0.3s ease;
        }
        .back-btn:hover { color: #ef4444; }
        
        .transactions-table {
            background: rgba(30, 41, 59, 0.8);
            border: 1px solid #334155;
            border-radius: 10px;
            padding: 20px;
            overflow-x: auto;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
        }
        
        th {
            background: rgba(51, 65, 85, 0.8);
            color: #ef4444;
            padding: 12px;
            text-align: left;
            font-weight: 600;
            border-bottom: 2px solid #475569;
        }
        
        td {
            padding: 12px;
            border-bottom: 1px solid #334155;
            color: #cbd5e1;
        }
        
        tr:hover { background: rgba(51, 65, 85, 0.3); }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>💰 Toutes les Transactions</h1>
            <a href="/x7k3j9m2l8n1p5r/dashboard" class="back-btn">← Retour Dashboard</a>
        </header>

        <div class="transactions-table">
            <table>
                <thead>
                    <tr>
                        <th>Date</th>
                        <th>Parrain</th>
                        <th>Forfait</th>
                        <th>Téléphone Client</th>
                        <th>Montant</th>
                        <th>Commission (5%)</th>
                        <th>Code WiFi</th>
                        <th>Statut</th>
                    </tr>
                </thead>
                <tbody>
                    {% for t in transactions %}
                    <tr>
                        <td>{{ t.date_creation.strftime('%d/%m/%Y %H:%M') }}</td>
                        <td>{{ t.parrain.prenom if t.parrain else 'Vente directe' }}</td>
                        <td>{{ t.forfait.nom }}</td>
                        <td>{{ t.client_telephone }}</td>
                        <td>{{ t.montant }} FCFA</td>
                        <td style="color: #4ade80;"><strong>{{ t.commission }} FCFA</strong></td>
                        <td><code>{{ t.code_wifi }}</code></td>
                        <td>{{ t.statut.upper() }}</td>
                    </tr>
                    {% else %}
                    <tr>
                        <td colspan="8" style="text-align: center; color: #64748b;">
                            Aucune transaction
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</body>
</html>
"""


# --- MAIN ---

if __name__ == '__main__':
    initialiser_base_donnees()
    app.run(debug=True, host='0.0.0.0', port=5000)
