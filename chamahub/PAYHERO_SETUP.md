# Payhero Integration Setup Guide

## 🔑 **Setting Up Payhero Credentials**

### **Method 1: Direct Configuration (Current)**
Your Payhero credentials are configured in `chamahub/settings.py`:

```python
# Payhero Configuration
PAYHERO_API_KEY = 'your_payhero_api_key_here'
PAYHERO_SECRET_KEY = 'your_payhero_secret_key_here'
PAYHERO_BASE_URL = 'https://api.payhero.com'
PAYHERO_WEBHOOK_SECRET = 'your_webhook_secret_here'
```

**Replace the placeholder values with your actual Payhero credentials.**

### **Method 2: Environment Variables (Recommended for Production)**

1. **Install python-decouple**:
   ```bash
   pip install python-decouple
   ```

2. **Create a `.env` file** in your project root:
   ```bash
   # .env file
   PAYHERO_API_KEY=your_actual_api_key
   PAYHERO_SECRET_KEY=your_actual_secret_key
   PAYHERO_BASE_URL=https://api.payhero.com
   PAYHERO_WEBHOOK_SECRET=your_actual_webhook_secret
   ```

3. **Update settings.py** to use environment variables:
   ```python
   from decouple import config
   
   # Payhero Configuration
   PAYHERO_API_KEY = config('PAYHERO_API_KEY', default='')
   PAYHERO_SECRET_KEY = config('PAYHERO_SECRET_KEY', default='')
   PAYHERO_BASE_URL = config('PAYHERO_BASE_URL', default='https://api.payhero.com')
   PAYHERO_WEBHOOK_SECRET = config('PAYHERO_WEBHOOK_SECRET', default='')
   ```

## 🔧 **Where to Get Payhero Credentials**

1. **Log into your Payhero dashboard**
2. **Navigate to API settings** or **Developer section**
3. **Generate API credentials**:
   - API Key
   - Secret Key
   - Webhook Secret (for validating incoming webhooks)

## 📡 **Integration Points**

### **1. Payment Initiation**
Update the `initiate_payhero_payment()` function in `core/views.py`:

```python
import requests
from django.conf import settings

def initiate_payhero_payment(request, payment_type, reference_id):
    # Payhero API call
    headers = {
        'Authorization': f'Bearer {settings.PAYHERO_API_KEY}',
        'Content-Type': 'application/json'
    }
    
    data = {
        'amount': amount,
        'currency': 'KES',
        'reference': reference,
        'callback_url': f'{request.build_absolute_uri("/webhook/payhero/")}'
    }
    
    response = requests.post(
        f'{settings.PAYHERO_BASE_URL}/payments',
        headers=headers,
        json=data
    )
    
    # Handle response and redirect to Payhero payment page
```

### **2. Webhook Validation**
Update the `payhero_webhook()` function in `core/views.py`:

```python
import hmac
import hashlib
from django.conf import settings

def payhero_webhook(request):
    # Validate webhook signature
    signature = request.headers.get('X-Payhero-Signature')
    payload = request.body
    
    expected_signature = hmac.new(
        settings.PAYHERO_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    if not hmac.compare_digest(signature, expected_signature):
        return JsonResponse({'error': 'Invalid signature'}, status=400)
    
    # Process webhook data
    data = json.loads(payload)
    # ... rest of webhook processing
```

## 🧪 **Testing Your Integration**

1. **Update credentials** in `settings.py`
2. **Test payment flow**:
   - Make a contribution
   - Check if Payhero payment page loads
   - Test webhook endpoint
3. **Monitor logs** for API responses
4. **Verify transaction status** updates

## 🔒 **Security Best Practices**

1. **Never commit credentials** to version control
2. **Use environment variables** in production
3. **Validate webhook signatures** always
4. **Use HTTPS** for all Payhero communications
5. **Rotate API keys** regularly

## 📞 **Payhero Support**

- **Documentation**: Check Payhero's API documentation
- **Support**: Contact Payhero support for integration help
- **Testing**: Use Payhero's sandbox environment for testing

## 🚀 **Next Steps**

1. **Get your Payhero credentials**
2. **Update the settings.py file**
3. **Test the payment flow**
4. **Implement webhook validation**
5. **Deploy to production**

Your ChamaHub application is ready for Payhero integration!
