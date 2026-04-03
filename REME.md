# 🔐 Flash WiFi - Système de Parrainage & Gestion Admin

Application Flask pour la gestion de ventes WiFi avec système de parrainage, commissions et admin sécurisé.

## 📋 Fonctionnalités

### 🌐 Public
- 💳 Achat de forfaits WiFi via FedaPay
- 🎁 Génération automatique de codes WiFi uniques (XXXX-XXXX-XXXX)

### 🤝 Parrainage
- ✅ Inscription sécurisée des parrains (code secret requis)
- 📝 Collecte d'informations personnelles complètes
- 🎯 Attribution de codes de parrainage uniques
- 📊 Dashboard personnel avec statistiques
- 💰 Commissions automatiques (5% par vente)
- ⚙️ Configuration des clés FedaPay personnelles
- 🔒 Mot de passe personnel sécurisé

### 👑 Admin Master (CACHÉ)
- URL: `/x7k3j9m2l8n1p5r/login` (secrète!)
- 👥 Gestion complète des parrains
- ✅ Activation/Suspension de comptes
- 📋 Consultation des infos personnelles (CNI, adresse, etc.)
- 💰 Suivi des ventes et commissions (5% à percevoir)
- 📊 Statistiques détaillées
- 🔍 Historique des transactions

## 🚀 Installation

### 1. Cloner/Créer le projet
```bash
mkdir flash-wifi
cd flash-wifi
```

### 2. Créer un environnement virtuel
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# ou
venv\Scripts\activate  # Windows
```

### 3. Installer les dépendances
```bash
pip install -r requirements.txt
```

### 4. Créer le fichier .env
```bash
cp .env.example .env
# Éditer .env avec vos configurations
```

### 5. Lancer l'application
```bash
python app.py
```

L'app sera disponible sur: `http://localhost:5000`

## 🔐 Routes principales

### 🌐 Public
| Route | Description |
|-------|-------------|
| `/` | Page d'accueil & forfaits |
| `/parrainage/inscription` | S'inscrire comme parrain |

### 🤝 Parrain
| Route | Description |
|-------|-------------|
| `/parrain/login` | Connexion parrain |
| `/parrain/dashboard` | Dashboard personnel |
| `/parrain/settings` | Paramètres (mot de passe, FedaPay) |

### 👑 Admin Master (CACHÉ)
| Route | Description |
|-------|-------------|
| `/x7k3j9m2l8n1p5r/login` | 🔒 Connexion admin |
| `/x7k3j9m2l8n1p5r/dashboard` | 📊 Dashboard |
| `/x7k3j9m2l8n1p5r/parrains` | 👥 Gérer parrains |
| `/x7k3j9m2l8n1p5r/parrain/<id>` | 📋 Détails parrain |
| `/x7k3j9m2l8n1p5r/transactions` | 💰 Toutes les transactions |

## 🔑 Identifiants par défaut

### Master Admin
- **URL**: `http://localhost:5000/x7k3j9m2l8n1p5r/login`
- **Mot de passe**: `MasterAdmin2024!` (changez le dans .env)

### Code Parrainage
- **Code**: `SECRET_PARRAINAGE_2024` (changez le dans .env)

## 💾 Base de données

La base de données SQLite est créée automatiquement au premier lancement.

### Modèles
- **Forfait**: Les forfaits WiFi disponibles
- **Parrain**: Les revendeurs
- **Transaction**: L'historique des ventes
- **AdminMaster**: L'administrateur principal

## 🛡️ Sécurité

✅ Mots de passe hashés (Werkzeug)
✅ Sessions sécurisées Flask
✅ URLs cachées pour l'admin
✅ Validation des inputs
✅ Code de parrainage secret
✅ Logging des actions

## 📱 Interface responsive

Tous les templates sont optimisés pour mobile et desktop.

## 🎨 Design

- **Theme**: Dark Mode moderne
- **Couleurs**: Jaune/Bleu/Rouge pour les hiérarchies
- **Animations**: Transitions fluides

## 📞 Support

Pour toute question ou problème, consultez les logs.

---

**Développé pour Flash WiFi - Bénin 🇧🇯**
