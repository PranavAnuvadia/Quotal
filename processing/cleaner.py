"""
cleaner.py - Hesitation and filler removal ported from simple-voice (sv-text).

Deterministically cleans speech transcripts before pasting:
- Strips filler sounds: "um", "uh", "hmm", "ah", "er", etc.
- Preserves real words: "err", "hum" (Hindi for "we").
- Cleans up trailing/hanging punctuation.
- Normalizes spacing and capitalizes sentences.
"""

import re

# Hesitation shapes (after lower-casing and collapsing runs of same letter)
SHAPES = {
    "um", "uhm", "uh", "ah", "ahm", "ahum", "eh", "er", "erm", "hm", "hmm"
}

# Real words that shouldn't be stripped
KEPT_WORDS = {"err", "hum"}

SENTENCE_END_CHARS = {'.', '?', '!'}
TRAILING_PUNCT = {',', '.', ';', ':', '?', '!', '-', '…'}


def collapse_repeated_letters(word: str) -> str:
    """Collapses consecutive repeated letters (e.g. 'ummmm' -> 'um', 'uhhhh' -> 'uh')."""
    if not word:
        return ""
    result = []
    for char in word.lower():
        if not result or result[-1] != char:
            result.append(char)
        elif len(result) >= 2 and result[-1] == char and result[-2] == char:
            continue
        else:
            result.append(char)
    # Simplify further to pure run collapse for hesitation matching
    collapsed = re.sub(r'(.)\1+', r'\1', word.lower())
    return collapsed


def is_hesitation(word: str) -> bool:
    """Check if a word is a hesitation sound."""
    core_word = word.strip().lower()
    if not core_word:
        return False
    if core_word in KEPT_WORDS:
        return False
    if core_word in SHAPES:
        return True
    
    # Check collapsed form (e.g. 'ummm' -> 'um')
    collapsed = collapse_repeated_letters(core_word)
    if collapsed in SHAPES and core_word not in KEPT_WORDS:
        return True
        
    # Check hyphenated hesitations (e.g. 'uh-um', 'e-au')
    if '-' in core_word:
        parts = core_word.split('-')
        return all(p in SHAPES or collapse_repeated_letters(p) in SHAPES for p in parts if p)
        
    return False


def strip_hesitations(text: str) -> str:
    """
    Strips hesitation tokens with attached punctuation.
    If a hesitation token at the end of a sentence carries terminal punctuation,
    transfers the punctuation to the previous word.
    """
    lines = text.split('\n')
    cleaned_lines = []
    
    for line in lines:
        words = line.split()
        if not words:
            continue
            
        kept_words = []
        for i, word in enumerate(words):
            # Extract punctuation attached to the word
            core = word.rstrip(',.;:?!-…')
            trailing = word[len(core):]
            
            if is_hesitation(core):
                # If this hesitation had a sentence-ending mark and there's a previous word,
                # attach the sentence-ending mark to the previous word.
                if kept_words and any(c in SENTENCE_END_CHARS for c in trailing):
                    end_char = next((c for c in reversed(trailing) if c in SENTENCE_END_CHARS), '.')
                    kept_words[-1] = kept_words[-1].rstrip(',;:-') + end_char
                continue
                
            kept_words.append(word)
            
        if kept_words:
            cleaned_lines.append(" ".join(kept_words))
            
    return "\n".join(cleaned_lines)


# ---------------------------------------------------------------------------
# Devanagari to Romanized Hinglish transliterator
# ---------------------------------------------------------------------------
_DEVA_VOWELS = {
    'अ': 'a', 'आ': 'aa', 'इ': 'i', 'ई': 'i', 'उ': 'u', 'ऊ': 'u',
    'ऋ': 'ri', 'ए': 'e', 'ऐ': 'ai', 'ओ': 'o', 'औ': 'au',
    'ऑ': 'o', 'ॲ': 'a', 'ऍ': 'a',
}
_DEVA_MATRAS = {
    'ा': 'a', 'ि': 'i', 'ी': 'i', 'ु': 'u', 'ू': 'u', 'ृ': 'ri',
    'े': 'e', 'ै': 'ai', 'ो': 'o', 'ौ': 'au',
    'ॉ': 'o', 'ॅ': 'a',
}
_DEVA_CONSONANTS = {
    'क': 'k', 'ख': 'kh', 'ग': 'g', 'घ': 'gh', 'ङ': 'ng',
    'च': 'ch', 'छ': 'chh', 'ज': 'j', 'झ': 'jh', 'ञ': 'ny',
    'ट': 't', 'ठ': 'th', 'ड': 'd', 'ढ': 'dh', 'ण': 'n',
    'त': 't', 'थ': 'th', 'द': 'd', 'ध': 'dh', 'न': 'n',
    'प': 'p', 'फ': 'f', 'ब': 'b', 'भ': 'bh', 'म': 'm',
    'य': 'y', 'र': 'r', 'ल': 'l', 'व': 'v', 'श': 'sh', 'ष': 'sh', 'स': 's', 'ह': 'h',
    'क़': 'q', 'ख़': 'kh', 'ग़': 'gh', 'ज़': 'z', 'ड़': 'r', 'ढ़': 'rh', 'फ़': 'f',
}
_HALANT = '्'
_ANUSVARA = 'ं'
_CHANDRABINDU = 'ँ'
_VISARGA = 'ः'
_NUKTA = '़'

# Common English loanwords written in Devanagari by Whisper
_DEVA_LOANWORDS = {
    'हे': 'hey', 'हेलो': 'hello', 'ब्रो': 'bro', 'ब्रू': 'bro',
    'टुडे': 'today', 'टुडेज़': "today's", 'यस्टरडे': 'yesterday', 'टुमारो': 'tomorrow',
    'वास': 'was', 'वर': 'were', 'इज़': 'is', 'ए': 'a', 'एन': 'an', 'द': 'the',
    'ग्रेट': 'great', 'डेवलप': 'day', 'डे': 'day', 'डेज़': 'days', 'गुड': 'good', 'बैड': 'bad', 'नाइस': 'nice', 'ऑसम': 'awesome',
    'आई': 'I', 'एम': 'am', 'टॉकिङ': 'talking', 'टौकिङ': 'talking', 'टॉकिंग': 'talking',
    'इन': 'in', 'इंग्लिश': 'English', 'एंड': 'and', 'इट्स': "it's",
    'गिविंग': 'giving', 'मी': 'me', 'दिस': 'this', 'डोंट': "don't",
    'नो': 'know', 'वाई': 'why', 'व्हॉट': 'what', 'व्हाट': 'what',
    'आर': 'are', 'यू': 'you', 'डूइंग': 'doing', 'करेंटली': 'currently', 'करंटली': 'currently',
    'स्टेलिंग': 'telling', 'टेलिंग': 'telling', 'नॉट': 'not', 'सेइंग': 'saying',
    'हिंदी': 'Hindi',
    'बिल्डिंग': 'building', 'बिल्डिंग्स': 'buildings',
    'फोन': 'phone', 'फ़ोन': 'phone',
    'कॉल': 'call', 'कॉल्स': 'calls', 'कॉलिंग': 'calling',
    'ऑफिस': 'office', 'ऑफ़िस': 'office',
    'मीटिंग': 'meeting', 'मीटिंग्स': 'meetings',
    'मैसेज': 'message', 'मैसेजेस': 'messages',
    'स्क्रीन': 'screen', 'कंप्यूटर': 'computer', 'लैपटॉप': 'laptop',
    'मोबाइल': 'mobile', 'इंटरनेट': 'internet', 'वीडियो': 'video', 'वीडियोज': 'videos',
    'फोटो': 'photo', 'फ़ोटो': 'photo', 'फोटोज़': 'photos',
    'क्लिक': 'click', 'चेक': 'check', 'सेंड': 'send', 'टाइम': 'time',
    'प्रॉब्लम': 'problem', 'प्रॉब्लम्स': 'problems',
    'इशू': 'issue', 'इशूज': 'issues', 'इशूज़': 'issues',
    'टेंशन': 'tension', 'हेल्प': 'help', 'प्लान': 'plan',
    'स्टार्ट': 'start', 'स्टॉप': 'stop', 'अपडेट': 'update', 'डाउनलोड': 'download',
    'ऑनलाइन': 'online', 'ऑफलाइन': 'offline', 'नंबर': 'number', 'डेटा': 'data',
    'फाइल': 'file', 'फ़ाइल': 'file', 'फोल्डर': 'folder', 'फ़ोल्डर': 'folder',
    'पासवर्ड': 'password', 'वेबसाइट': 'website', 'लिंक': 'link',
    'डॉक्टर': 'doctor', 'हॉस्पिटल': 'hospital', 'स्कूल': 'school', 'कॉलेज': 'college',
    'ट्रेन': 'train', 'टिकट': 'ticket', 'टिकट्स': 'tickets',
    'कार': 'car', 'बस': 'bus', 'रोड': 'road', 'स्टेशन': 'station',
    'फ्लाइट': 'flight', 'एयरपोर्ट': 'airport', 'होटल': 'hotel', 'रूम': 'room',
    'बिल': 'bill', 'पेमेंट': 'payment', 'कैश': 'cash', 'कार्ड': 'card', 'बैंक': 'bank',
    'अकाउंट': 'account', 'ऑर्डर': 'order', 'डिलीवरी': 'delivery',
    'एड्रेस': 'address', 'लोकेशन': 'location', 'गूगल': 'Google', 'व्हाट्सएप': 'WhatsApp',
    'ऐप': 'app', 'ऐप्स': 'apps', 'सॉफ्टवेयर': 'software', 'सिस्टम': 'system',
    'नेटवर्क': 'network', 'वाईफाई': 'WiFi', 'बैटरी': 'battery', 'कैमरा': 'camera',
    'माइक': 'mic', 'साउंड': 'sound', 'ऑडियो': 'audio', 'गेम': 'game', 'टीम': 'team',
    'मैच': 'match', 'पार्टी': 'party', 'फ्रेंड': 'friend', 'फैमिली': 'family',
    'बॉस': 'boss', 'क्लाइंट': 'client', 'कस्टमर': 'customer', 'मैनेजर': 'manager',
    'जॉब': 'job', 'इंटरव्यू': 'interview', 'सैलरी': 'salary', 'बिजनेस': 'business',
    'मार्केट': 'market', 'शॉप': 'shop', 'शॉपिंग': 'shopping', 'ऑफर': 'offer',
    'प्राइस': 'price', 'रेट': 'rate', 'सर्विस': 'service', 'सपोर्ट': 'support',
    'रिव्यू': 'review', 'रिपोर्ट': 'report', 'प्रोजेक्ट': 'project', 'टास्क': 'task',
    'टारगेट': 'target', 'गोल': 'goal', 'आइडिया': 'idea', 'डिजाइन': 'design',
    'टेस्ट': 'test', 'रिजल्ट': 'result', 'एरर': 'error', 'बग': 'bug', 'फिक्स': 'fix',
    'रेडी': 'ready', 'डन': 'done', 'कैंसिल': 'cancel', 'कन्फर्म': 'confirm',
    'डिलीट': 'delete', 'सेव': 'save', 'एडिट': 'edit', 'कॉपी': 'copy', 'पेस्ट': 'paste',
    'शेयर': 'share', 'पोस्ट': 'post', 'लाइक': 'like', 'सब्सक्राइब': 'subscribe',
    'कमेंट': 'comment', 'फॉलो': 'follow', 'चैट': 'chat', 'वॉइस': 'voice',
    'क्लास': 'class', 'कोर्स': 'course', 'एग्जाम': 'exam', 'प्लीज': 'please',
    'थैंक्स': 'thanks', 'थैंक': 'thank', 'यू': 'you', 'थैंक्यू': 'thank you', 'थैंकयू': 'thank you',
    'सॉरी': 'sorry', 'ओके': 'okay', 'श्योर': 'sure', 'कूल': 'cool',
    'बुक': 'book', 'बुकिंग': 'booking', 'बुक्ड': 'booked',
    'रीसेट': 'reset', 'रिसेट': 'reset', 'ओपन': 'open', 'क्लोज': 'close',
    'सर्च': 'search', 'टाइप': 'type', 'प्रेस': 'press',
    'बैक': 'back', 'नेक्स्ट': 'next', 'वेट': 'wait', 'फास्ट': 'fast', 'स्लो': 'slow',
    'ईमेल': 'email', 'मेल': 'mail', 'ट्राई': 'try', 'ड्राइव': 'drive',
    'अपॉइंटमेंट': 'appointment'
}

# Standard Hindi words mapped to natural Romanized Hinglish spelling
_DEVA_COMMON_WORDS = {
    'मैंने': 'maine', 'मैने': 'maine', 'तूने': 'tune', 'तोने': 'tune', 'तुने': 'tune',
    'तुझे': 'tujhe', 'तुहें': 'tujhe', 'तुम्हें': 'tumhe', 'तुमने': 'tumne', 'तुम': 'tum',
    'मुझे': 'mujhe', 'मुझको': 'mujhko', 'मुझसे': 'mujhse', 'मुझपे': 'mujhpe',
    'तुझसे': 'tujhse', 'तुझपे': 'tujhpe', 'तुमसे': 'tumse',
    'उठाया': 'uthaya', 'उठायान': 'uthaya', 'उठायान्': 'uthaya',
    'उठाई': 'uthayi', 'उठाए': 'uthaye', 'उठाना': 'uthana', 'उठाओ': 'uthao',
    'काम': 'kaam', 'कुछ': 'kuch', 'हमें': 'hume', 'हमे': 'hume', 'में': 'mein', 'मे': 'mein',
    'तोड़ना': 'todna', 'तोड़ना': 'todna', 'तोडना': 'todna', 'तोड़े': 'tode', 'तोड़ा': 'toda',
    'देख': 'dekh', 'देखो': 'dekho', 'देखा': 'dekha', 'देखते': 'dekhte',
    'ऐसा': 'aisa', 'ऐसी': 'aisi', 'ऐसे': 'aise', 'कि': 'ki', 'को': 'ko',
    'है': 'hai', 'हैं': 'hain', 'हो': 'ho', 'हों': 'hoon', 'हूं': 'hoon', 'हूँ': 'hoon',
    'था': 'tha', 'थी': 'thi', 'थे': 'the',
    'कर': 'kar', 'रहा': 'raha', 'रहे': 'rahe', 'रही': 'rahi', 'करो': 'karo', 'करें': 'karein',
    'भाई': 'bhai', 'मेरे': 'mere', 'मेरा': 'mera', 'मेरी': 'meri',
    'तुम्हारा': 'tumhara', 'तुम्हारी': 'tumhari', 'तुम्हारे': 'tumhare',
    'आपका': 'aapka', 'आपकी': 'aapki', 'आपके': 'aapke', 'आपने': 'aapne',
    'उसका': 'uska', 'उसकी': 'uski', 'उसके': 'uske', 'उसने': 'usne',
    'इनका': 'inka', 'इनकी': 'inki', 'इनके': 'inke', 'इन्होंने': 'inhone',
    'उनका': 'unka', 'उनकी': 'unki', 'उनके': 'unke', 'उन्होंने': 'unhone',
    'बात': 'baat', 'बताओ': 'batao', 'बोलो': 'bolo', 'सुनो': 'suno', 'समझ': 'samajh',
    'चाहिए': 'chahiye', 'सकते': 'sakte', 'सकता': 'sakta', 'सकती': 'sakti',
    'जाना': 'jaana', 'जाओ': 'jaao', 'गया': 'gaya', 'गई': 'gayi', 'गए': 'gaye',
    'आना': 'aana', 'आओ': 'aao', 'आया': 'aaya', 'आई': 'aayi', 'आए': 'aaye',
    'करना': 'karna', 'होना': 'hona', 'लेना': 'lena', 'देना': 'dena',
    'बोला': 'bola', 'बोली': 'boli', 'बोले': 'bole',
    'कहा': 'kaha', 'किया': 'kiya', 'दिया': 'diya', 'लिया': 'liya',
    'नहीं': 'nahi', 'नहि': 'nahi', 'हाँ': 'haan', 'हां': 'haan', 'और': 'aur',
    'ही': 'hi', 'भी': 'bhi',
    'बहुत': 'bahut', 'ज्यादा': 'zyada', 'कम': 'kam',
    'अच्छा': 'achha', 'अच्छी': 'achhi', 'अच्छे': 'achhe',
    'बुरा': 'bura', 'बड़ा': 'bada', 'छोटा': 'chhota', 'नया': 'naya', 'पुराना': 'purana',
    'आज': 'aaj', 'कल': 'kal', 'परसों': 'parso', 'अभी': 'abhi', 'बाद': 'baad',
    'पहले': 'pehle', 'यहाँ': 'yahan', 'यहाँपे': 'yahanpe', 'वहाँ': 'wahan', 'वहाँपे': 'wahanpe',
    'कहाँ': 'kahan', 'जहाँ': 'jahan',
    'कौन': 'kaun', 'क्या': 'kya', 'क्यों': 'kyun', 'कैसे': 'kaise',
    'कितना': 'kitna', 'कितनी': 'kitni', 'कितने': 'kitne',
    'जब': 'jab', 'तब': 'tab', 'कब': 'kab', 'सब': 'sab', 'लोग': 'log',
    'पास': 'paas', 'साथ': 'saath', 'ठीक': 'theek',
    'वह': 'woh', 'यह': 'yeh', 'वो': 'woh', 'ये': 'yeh',
    'इसे': 'ise', 'उसे': 'use',
    'चिंता': 'chinta', 'मत': 'mat',
    'रास्ते': 'raaste', 'थोड़ी': 'thodi', 'थोड़ा': 'thoda',
    'पहुंचना': 'pahunchna', 'पहुंच': 'pahunch', 'पहुँचूँगा': 'pahuchunga', 'पहुंचूंगा': 'pahuchunga',
    'गाड़ी': 'gaadi', 'चल': 'chal', 'चलो': 'chalo', 'चलते': 'chalte',
    # Gujarati common words (mapped to Devanagari)
    'केम': 'kem', 'छो': 'chho', 'शुं': 'shun', 'करे': 'kare', 'छे': 'chhe',
    'तुं': 'tun', 'तमे': 'tame', 'मने': 'mane', 'काले': 'kale', 'आजे': 'aaje',
    'मजामां': 'majama', 'मजामा': 'majama', 'सारुं': 'saru', 'क्यां': 'kyan', 'क्यारे': 'kyare',
    # Marathi common words
    'काय': 'kay', 'करतोस': 'karatos', 'करतेस': 'karates', 'करतो': 'karato', 'करतात': 'karatat',
    'मला': 'mala', 'तुला': 'tula', 'त्याला': 'tyala', 'तिला': 'tila',
    'आम्हाला': 'aamhala', 'तुम्हाला': 'tumhala', 'उद्या': 'udya',
    'आहे': 'aahe', 'आहेत': 'aahet', 'नाही': 'nahi', 'झाले': 'jhale', 'झाला': 'jhala', 'झाली': 'jhali',
    'कसा': 'kasa', 'कशी': 'kashi', 'कसे': 'kase'
}

_URDU_WORDS = {
    "کیا": "kya", "کر": "kar", "رہا": "raha", "رہے": "rahe", "رہی": "rahi",
    "ہے": "hai", "ہیں": "hain", "ہو": "ho", "ہوں": "hoon", "تھا": "tha",
    "تھی": "thi", "تھے": "the", "میں": "main", "آپ": "aap", "تم": "tum",
    "ہم": "hum", "وہ": "woh", "یہ": "yeh", "کیسے": "kaise", "کیسی": "kaisi",
    "کب": "kab", "کہاں": "kahan", "کیوں": "kyun", "کون": "kaun", "ٹھیک": "theek",
    "اچھا": "achha", "نہیں": "nahi", "ہاں": "haan", "اور": "aur", "بات": "baat",
    "کام": "kaam", "کرو": "karo", "کریں": "karein", "بول": "bol", "سنو": "suno"
}

_URDU_CHARS = {
    'ا': 'a', 'آ': 'aa', 'ب': 'b', 'پ': 'p', 'ت': 't', 'ٹ': 't', 'ث': 's',
    'ج': 'j', 'چ': 'ch', 'ح': 'h', 'خ': 'kh', 'د': 'd', 'ڈ': 'd', 'ذ': 'z',
    'ر': 'r', 'ڑ': 'r', 'ز': 'z', 'ژ': 'zh', 'س': 's', 'ش': 'sh', 'ص': 's',
    'ض': 'z', 'ط': 't', 'ظ': 'z', 'ع': 'a', 'غ': 'gh', 'ف': 'f', 'ق': 'q',
    'ک': 'k', 'گ': 'g', 'ل': 'l', 'م': 'm', 'ن': 'n', 'ں': 'n', 'و': 'o',
    'ہ': 'h', 'ۂ': 'h', 'ۃ': 't', 'ھ': 'h', 'ی': 'i', 'ے': 'e', 'ء': ''
}


def urdu_to_hinglish(text: str) -> str:
    """Fallback transliterator for Perso-Arabic Urdu script into Romanized Hinglish."""
    if not text or not any('\u0600' <= ch <= '\u06FF' for ch in text):
        return text
    tokens = re.findall(r'[\u0600-\u06FF]+|[^\u0600-\u06FF]+', text)
    out = []
    for tok in tokens:
        if re.search(r'[\u0600-\u06FF]', tok):
            words = tok.split()
            w_out = []
            for w in words:
                clean_w = re.sub(r'[^\u0600-\u06FF]', '', w)
                if clean_w in _URDU_WORDS:
                    w_out.append(_URDU_WORDS[clean_w])
                else:
                    trans = ''.join(_URDU_CHARS.get(c, '') for c in clean_w)
                    w_out.append(trans if trans else w)
            out.append(' '.join(w_out))
        else:
            out.append(tok)
    return ''.join(out)


def gujarati_to_devanagari(text: str) -> str:
    """Converts Gujarati Unicode characters to their corresponding Devanagari equivalents."""
    if not text or not any('\u0A80' <= ch <= '\u0AFF' for ch in text):
        return text
    out = []
    for ch in text:
        code = ord(ch)
        if 0x0A80 <= code <= 0x0AFF:
            # Gujarati to Devanagari is an exact -0x180 offset in Unicode standard
            out.append(chr(code - 0x180))
        else:
            out.append(ch)
    return ''.join(out)


def indic_to_roman(text: str) -> str:
    """Converts Indic scripts (Hindi, Marathi, Gujarati, Urdu) to clean Romanized text."""
    if not text:
        return text

    # Handle Urdu/Arabic script fallback
    if any('\u0600' <= ch <= '\u06FF' for ch in text):
        text = urdu_to_hinglish(text)

    # Handle Gujarati script by mapping to Devanagari
    if any('\u0A80' <= ch <= '\u0AFF' for ch in text):
        text = gujarati_to_devanagari(text)

    if not any('\u0900' <= ch <= '\u097F' for ch in text):
        return text

    words = text.split(" ")
    out_words = []
    
    for word in words:
        if not any('\u0900' <= ch <= '\u097F' for ch in word):
            out_words.append(word)
            continue

        # Match punctuation boundaries around the Devanagari word
        lead_m = re.match(r'^[^\w\u0900-\u097F]+', word)
        lead = lead_m.group(0) if lead_m else ""
        tail_m = re.search(r'[^\w\u0900-\u097F]+$', word)
        tail = tail_m.group(0) if tail_m else ""
        clean_w = re.sub(r'^[^\w\u0900-\u097F]+|[^\w\u0900-\u097F]+$', '', word)

        # 1. Check English loanwords and common Hinglish dictionary lookups
        if clean_w in _DEVA_LOANWORDS:
            out_words.append(lead + _DEVA_LOANWORDS[clean_w] + tail)
            continue
        if clean_w in _DEVA_COMMON_WORDS:
            out_words.append(lead + _DEVA_COMMON_WORDS[clean_w] + tail)
            continue

        res = []
        chars = list(clean_w)
        n = len(chars)
        i = 0
        while i < n:
            ch = chars[i]
            if i + 1 < n and chars[i+1] == _NUKTA:
                ch = ch + _NUKTA
                i += 1
                
            if ch in _DEVA_VOWELS:
                res.append(_DEVA_VOWELS[ch])
            elif ch in _DEVA_CONSONANTS:
                base = _DEVA_CONSONANTS[ch]
                if i + 1 < n:
                    next_ch = chars[i+1]
                    if next_ch == _HALANT:
                        res.append(base)
                        i += 1
                    elif next_ch in _DEVA_MATRAS:
                        res.append(base + _DEVA_MATRAS[next_ch])
                        i += 1
                    elif next_ch in (_ANUSVARA, _CHANDRABINDU):
                        res.append(base + "n")
                        i += 1
                    elif chars[i+1] in _DEVA_CONSONANTS:
                        res.append(base + "a")
                    else:
                        res.append(base)
                else:
                    res.append(base)
            elif ch in _DEVA_MATRAS:
                res.append(_DEVA_MATRAS[ch])
            elif ch in (_ANUSVARA, _CHANDRABINDU):
                res.append("n")
            elif ch == _VISARGA:
                res.append("h")
            elif ch == _HALANT:
                pass
            else:
                res.append(ch)
            i += 1
            
        out_word = "".join(res)
        out_word = re.sub(r'a{3,}', 'aa', out_word)
        out_words.append(lead + out_word + tail)
        
    res_text = " ".join(out_words)
    replacements = {
        r'\bmainne\b': 'maine',
        r'\btoone\b': 'tune',
        r'\btone\b': 'tune',
        r'\btuhen\b': 'tujhe',
        r'\btumhen\b': 'tumhe',
        r'\btumane\b': 'tumne',
        r'\buthayan\b': 'uthaya',
        r'\bhee\b': 'hi',
        r'\bbhee\b': 'bhi',
        r'\bnahee\b': 'nahi',
        r'\btheek\b': 'theek',
        r'\bthik\b': 'theek',
        r'\bvah\b': 'woh',
        r'\byah\b': 'yeh',
        r'\bapana\b': 'apna',
        r'\bapane\b': 'apne',
        r'\bapani\b': 'apni',
        r'\bjald\b': 'jaldi',
        r'\bkuchh\b': 'kuch',
        r'\bhamen\b': 'hume',
        r'\bhame\b': 'hume',
        r'\bbilding\b': 'building',
        r'\bbildings\b': 'buildings',
        r'\bphon\b': 'phone',
        r'\bkaal\b': 'call',
        r'\btorana\b': 'todna',
        r'\btodana\b': 'todna',
        r'\bkam\b': 'kaam',
        r'\bofis\b': 'office',
        r'\bmiting\b': 'meeting',
        r'\bmesij\b': 'message',
        r'\bskreen\b': 'screen',
        r'\bproblam\b': 'problem',
        r'\bdaaktar\b': 'doctor',
        r'\bhaspataal\b': 'hospital',
        r'\btikat\b': 'ticket',
        r'\bkya\b': 'kya',
        r'\bkar\b': 'kar',
        r'\brahe\b': 'rahe',
        r'\braha\b': 'raha',
        r'\brahee\b': 'rahi',
        r'\bhai\b': 'hai',
        r'\bhain\b': 'hain',
        r'\bho\b': 'ho',
        r'\bbhai\b': 'bhai',
        r'\baap\b': 'aap',
        r'\btum\b': 'tum',
        r'\bmain\b': 'main',
        r'\bhum\b': 'hum',
        r'\bkaise\b': 'kaise',
        r'\bkahan\b': 'kahan',
        r'\bkyun\b': 'kyun',
    }
    for pat, rep in replacements.items():
        res_text = re.sub(pat, rep, res_text, flags=re.IGNORECASE)
        
    return res_text

# Backward compatibility alias
devanagari_to_hinglish = indic_to_roman


def clean_text(text: str) -> str:
    """
    Main clean pipeline:
    1. Strip hesitations (um, uh, etc.)
    2. Normalize whitespace
    3. Capitalize first letter of sentences
    4. Clean dangling commas/spaces
    """
    if not text or not text.strip():
        return ""
        
    # Strip hesitations
    cleaned = strip_hesitations(text)
    
    # Normalize spaces
    cleaned = re.sub(r'[ \t]+', ' ', cleaned).strip()
    
    # Remove leading punctuation (like hanging commas or periods)
    cleaned = re.sub(r'^[,\s;:-]+', '', cleaned)
    
    # Capitalize first character if it's an ASCII letter
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
        
    # Capitalize after sentence ends (. ? !)
    def cap_after_period(match):
        return match.group(1) + match.group(2).upper()
        
    cleaned = re.sub(r'([.?!]\s+)([a-z])', cap_after_period, cleaned)
    
    return cleaned.strip()


if __name__ == "__main__":
    # Test cases
    samples = [
        "um so aaj milte hai, okay?",
        "uhh kal meeting hai, um wait, parso hai.",
        "haan ummm theek hai, uh let's go.",
        "hum log aaj jayenge.",  # "hum" must stay!
        "err I think that's wrong.",  # "err" must stay!
    ]
    for s in samples:
        print(f"RAW:   {s}")
        print(f"CLEAN: {clean_text(s)}\n")
