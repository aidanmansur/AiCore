# AICore – Smart AI Assistant

**Pulsuz** Gemini API ilə işləyən GenAI chatbot. İstənilən suala cavab verir, söhbətin kontekstini xatırlayır və göndərdiyiniz şəkilləri ətraflı analiz edir (nəyə aid olduğu, xeyirli və zərərli cəhətlər, tövsiyələr).

## Xüsusiyyətlər

- Real GenAI cavabları, söz-söz axın (streaming) rejimində
- Söhbət tarixçəsi (hər istifadəçi üçün ayrı sessiya)
- Şəkil analizi: 📎 düyməsi, Ctrl+V ilə yapışdırma və ya sürüklə-burax. Eyni anda 4 şəkil (JPG, PNG, GIF, WEBP)
- Böyük şəkillər brauzerdə avtomatik kiçildilir
- Markdown cavablar: başlıq, siyahı, cədvəl, kod bloku, "Kopyala" düyməsi
- "Dayandır" və "Yeni söhbət" düymələri, mobil uyğunluq
- Təhlükəsizlik: şəkilin real tipi yoxlanılır, rate limit, XSS qorunması, API açarı yalnız serverdə
- Provayder dəyişdirilə bilər: Gemini (pulsuz, standart) və ya Claude (pullu)

## 1. Pulsuz API açarı almaq

1. https://aistudio.google.com/apikey ünvanını açın və Google hesabınızla daxil olun.
2. **Create API key** düyməsini basın və açarı kopyalayın.

Kart və ödəniş məlumatı tələb olunmur.

## 2. Quraşdırma

Python 3.9 və ya daha yeni versiya lazımdır.

```bash
# Virtual mühit (tövsiyə olunur)
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux

pip install -r requirements.txt

copy .env.example .env         # Windows
# cp .env.example .env         # macOS / Linux
```

`.env` faylını açın və `GEMINI_API_KEY=` sətrinə açarınızı yazın. Sonra:

```bash
python app.py
```

Brauzerdə http://127.0.0.1:5000 ünvanını açın. Yuxarı sağdakı status "Online" göstərirsə, hər şey hazırdır.

## Pulsuz səviyyənin məhdudiyyətləri

- **Limitlər:** Google pulsuz səviyyənin dəqiqəlik və günlük limitlərini artıq açıq yazmır, onları AI Studio-da layihənizin limit səhifəsində görə bilərsiniz. Limit dolanda bot "Pulsuz limit müvəqqəti dolub" mesajı göstərir, bir az gözləmək kifayətdir. Günlük limit gecə yarısı (Pasifik vaxtı ilə) sıfırlanır.
- **Məxfilik:** Google-un şərtlərinə görə pulsuz səviyyədə göndərilən məzmun onların məhsullarını yaxşılaşdırmaq üçün istifadə oluna bilər. Buna görə şəxsi sənədlərin, kartların, tibbi və ya məxfi şəkillərin yüklənməsini istifadəçilərə tövsiyə etməyin. Bu məsələ vacibdirsə, pullu səviyyəyə keçin.
- **Modellər:** Pulsuz əlçatan modellər zamanla dəyişə bilər. `GEMINI_MODEL` tapılmasa, proqram `GEMINI_FALLBACK_MODELS` siyahısından növbəti modeli avtomatik yoxlayır. Cari modellər üçün Google-un "Models" səhifəsinə baxın.
- **Şəkil ölçüsü:** şəkil başına maksimum 3,5 MB (brauzer böyük şəkilləri özü kiçildir).

## Claude-a keçmək (pullu, istəyə bağlı)

```bash
pip install anthropic
```

`.env` faylında `PROVIDER=claude` yazın, `ANTHROPIC_API_KEY` və `ANTHROPIC_MODEL` sətirlərini açıb doldurun.

## Layihənin strukturu

```
AICore/
├── app.py              # Flask backend: API, sessiya, şəkil yoxlanışı, streaming
├── providers.py        # Gemini (pulsuz) və Claude provayderləri
├── prompts.py          # Sistem promptu (botun davranışı və şəkil analizi formatı)
├── requirements.txt
├── .env.example        # Konfiqurasiya nümunəsi
├── templates/index.html
├── static/
│   ├── style.css
│   ├── script.js       # Chat məntiqi, şəkil əlavə etmə, axın qəbulu
│   └── markdown.js     # Təhlükəsiz Markdown renderer
└── tests/              # 37 avtomatik test (API açarı və internet tələb etmir)
```

## Botu fərdiləşdirmək

- **Şəkil analizi formatını və ya bot şəxsiyyətini dəyişmək** üçün `prompts.py` faylındakı `SYSTEM_PROMPT` mətnini redaktə edin.
- **Cavab uzunluğu:** `.env`-də `MAX_OUTPUT_TOKENS` (standart 4096).
- **Limitlər:** `app.py`-nin yuxarısında `MAX_IMAGES`, `MAX_MESSAGE_CHARS`, `MAX_HISTORY_MESSAGES` kimi sabitlər var.
- **Rate limit:** `RATE_LIMIT_PER_MINUTE` (standart 10). Pulsuz Gemini limitinə uyğun saxlayın.

## Testlər

```bash
python -m unittest discover -s tests -v
```

## Real serverə (production) çıxarmazdan əvvəl

1. `python app.py` yalnız lokal işləmək üçündür. Real serverdə `gunicorn` kimi WSGI serverindən və HTTPS-dən istifadə edin, `FLASK_DEBUG=0` saxlayın.
2. `.env`-də `SECRET_KEY` mütləq uzun təsadüfi mətn olsun.
3. Pulsuz limit **bütün istifadəçilər üçün ortaqdır** (hamı eyni açardan istifadə edir). Çox istifadəçi olarsa, limit tez dolacaq və pullu səviyyə lazım olacaq.
4. Söhbət tarixçəsi və rate limit server yaddaşındadır, server yenidən başlayanda sıfırlanır. Bir neçə worker işlətmək lazım gələrsə, Redis və ya verilənlər bazasına keçirin.
5. Süni intellekt səhv edə bilər. Sistem promptu botu sağlamlıq, hüquq və təhlükəsizlik mövzularında ehtiyatlı olmağa yönəldir, səhifənin altında da xəbərdarlıq var.
