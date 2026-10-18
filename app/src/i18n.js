/*
The app's own language : English, or Arabic with the whole app laid
out right to left, both windows and every theme.

The pages are written in English, and this file translates what they show instead of every script
asking for each string: a dictionary keyed by the English text is applied to text nodes,
placeholders and titles as they appear (a MutationObserver sees every change the scripts make),
and patterns cover the sentences built around a number or a name. Switching back restores the
English each node came with. What is not the app's own words (the question, the suggested lines,
role names) is marked translate="no" and left alone: a keyword line reading "Education" is an
answer, not the name of a section.

A classic script loaded in <head>, so the language chosen last is applied while the page is parsed
and nothing flashes in English first; the engine's settings then confirm it through setLang. Text
the engine writes itself (its error messages) stays English. tests/test_ui_language.py checks that
every string in both pages has a translation here.
*/
(() => {
  const AR = {
    // ---- overlay ----
    "Fawa2ly settings": "إعدادات فوقلي",
    "Settings": "الإعدادات",
    "Minimize (click Fawa2ly in the taskbar to bring it back)": "تصغير (اضغط على فوقلي في شريط المهام لإعادتها)",
    "Close": "إغلاق",
    "Minimize": "تصغير",
    "Maximize": "تكبير",
    "Connecting to engine": "جارٍ الاتصال بالمحرك",
    "No background document is loaded: answers cannot use your experience": "لا يوجد مستند خلفية: الإجابات لن تستخدم خبرتك",
    "No CV": "لا توجد سيرة ذاتية",
    "Clicks go to the window underneath": "النقرات تذهب إلى النافذة التي تحتها",
    "Click-through: Ctrl+Shift+L": "تمرير النقرات: Ctrl+Shift+L",
    "Switch model for the next question": "تغيير النموذج للسؤال التالي",
    "Pause": "إيقاف مؤقت",
    "Resume": "استئناف",
    "End": "إنهاء",
    "Fawa2ly cannot hear the call yet.": "فوقلي لا يسمع المكالمة بعد.",
    "Waiting on the local engine. Nothing will be suggested until this connects.": "في انتظار المحرك المحلي. لن يظهر أي اقتراح حتى يتصل.",
    "Reconnect": "إعادة الاتصال",
    "New call": "مكالمة جديدة",
    "Set up this call": "إعداد هذه المكالمة",
    "On demand": "عند الطلب",
    "What should I say now?": "ماذا أقول الآن؟",
    "Recap so far": "ملخص ما دار حتى الآن",
    "Dynamic: pick the best of the three per question": "تلقائي: اختيار الأنسب من الثلاثة لكل سؤال",
    "The shape Dynamic chose for this answer": "الشكل الذي اختاره الوضع التلقائي لهذه الإجابة",
    "Lines": "جمل",
    "Keywords": "كلمات مفتاحية",
    "Direction": "توجيه",
    "Call": "المكالمة",
    "Interview": "مقابلة عمل",
    "Role": "الدور",
    "No role": "بدون دور",
    "Change in Settings": "تغييره من الإعدادات",
    "Language": "لغة المكالمة",
    "English": "الإنجليزية",
    "Arabic, Egyptian": "العربية، اللهجة المصرية",
    "Arabic": "العربية",
    "Auto: English and 9 other languages, no Arabic": "تلقائي: الإنجليزية و9 لغات أخرى، بدون العربية",
    "Model": "النموذج",
    "Ask the model early: faster, but more requests (off for rate-limited free tiers)": "اسأل النموذج مبكرًا: أسرع، لكن بطلبات أكثر (أوقفه مع الباقات المجانية المحدودة)",
    "Answers": "الإجابات",
    "Full lines to say": "جمل كاملة تقولها",
    "Keywords only": "كلمات مفتاحية فقط",
    "Direction: how to shape the answer": "توجيه: كيف تبني الإجابة",
    "Transcription": "تحويل الكلام إلى نص",
    "Deepgram: online, fastest": "Deepgram: عبر الإنترنت، الأسرع",
    "Local Whisper: offline, no key": "Whisper المحلي: بدون إنترنت وبدون مفتاح",
    "Local model size": "حجم النموذج المحلي",
    "Tiny: fastest, least accurate": "صغير جدًا: الأسرع والأقل دقة",
    "Base": "أساسي",
    "Small: the default": "صغير: الافتراضي",
    "Medium: most accurate, slowest": "متوسط: الأدق والأبطأ",
    "Transcribes on this computer, so no audio leaves it and no Deepgram key is needed. A question is judged only once the other side stops speaking, so the lines come later than with Deepgram. The model downloads once, on the first call (about 480 MB for Small). A model has to transcribe faster than people talk, so choose a smaller one if the lines fall behind. Pair it with Ollama under API keys for a call that sends nothing off this computer.": "يتم التحويل على هذا الكمبيوتر، فلا يخرج منه أي صوت ولا حاجة إلى مفتاح Deepgram. لا يُحكم على السؤال إلا بعد أن يتوقف الطرف الآخر عن الكلام، فتظهر السطور متأخرة عن Deepgram. يُنزَّل النموذج مرة واحدة عند أول مكالمة (نحو 480 ميغابايت للحجم الصغير). يجب أن يحوّل النموذج الكلام أسرع من سرعة حديث الناس، فاختر نموذجًا أصغر إذا تأخرت السطور. اجمعه مع Ollama من مفاتيح API لمكالمة لا يخرج منها شيء من هذا الكمبيوتر.",
    "The libraries for local transcription are not installed in this build, so a call would fail to start.": "مكتبات التحويل المحلي غير مثبتة في هذه النسخة، لذلك ستفشل المكالمة في البدء.",
    "Also transcribe my microphone, for context": "حوّل كلامي من الميكروفون إلى نص أيضًا، للسياق",
    "Your background": "خلفيتك",
    "No document. Suggestions will be general.": "لا يوجد مستند. الاقتراحات ستكون عامة.",
    "No CV loaded. The model cannot use your experience, and every answer will be generic.": "لم تُحمَّل سيرة ذاتية. النموذج لن يستطيع استخدام خبرتك، وكل إجابة ستكون عامة.",
    "Choose files": "اختيار ملفات",
    "Add more": "إضافة المزيد",
    "Remove all": "إزالة الكل",
    "Start listening": "ابدأ الاستماع",
    "Starting the call": "جارٍ بدء المكالمة",
    "Start without a CV": "ابدأ بدون سيرة ذاتية",
    "No document. Suggestions will not state prices, terms or features.": "لا يوجد مستند. الاقتراحات لن تذكر أسعارًا أو شروطًا أو مزايا.",
    "No model has a key yet: add one to .env": "لا يوجد نموذج له مفتاح بعد: أضف مفتاحًا في .env",
    "Asked": "السؤال",
    "Previous question (Left arrow)": "السؤال السابق (السهم الأيسر)",
    "Next question (Right arrow)": "السؤال التالي (السهم الأيمن)",
    "Latest": "الأحدث",
    "Call summary": "ملخص المكالمة",
    "writing the next line": "يكتب السطر التالي",
    "More on this": "المزيد عن هذا",
    "No lines for this one.": "لا توجد سطور لهذا السؤال.",
    "That one did not come back.": "لم تصل الإجابة لهذا السؤال.",
    "Keep talking, earlier answers are still below.": "استمر في الكلام، الإجابات السابقة ما زالت بالأسفل.",
    "Retry": "إعادة المحاولة",
    "Earlier": "السابق",
    "This call": "هذه المكالمة",
    "Show this question": "عرض هذا السؤال",
    "no audio": "لا يوجد صوت",
    // the settings section and the overlay's state: one noun fits both
    "Listening": "الاستماع",
    "listening": "يستمع",
    "not listening": "لا يستمع",
    "Not in a call": "لست في مكالمة",
    "Nothing asked yet. Lines appear here the moment a question is heard.": "لم يُسأل شيء بعد. تظهر السطور هنا بمجرد سماع سؤال.",
    "Paused, not listening": "متوقف مؤقتًا، لا يستمع",
    "captions off while paused": "النص المباشر متوقف أثناء الإيقاف المؤقت",
    "Call ended.": "انتهت المكالمة.",
    "Call ended": "انتهت المكالمة",
    "This session is finished.": "انتهت هذه الجلسة.",
    "Engine offline": "المحرك غير متصل",
    "Fawa2ly is not listening.": "فوقلي لا يستمع.",
    "The connection dropped, so nothing on this call is being heard or suggested.": "انقطع الاتصال، لذلك لا يُسمع ولا يُقترح شيء في هذه المكالمة.",
    "captions stopped": "توقف النص المباشر",

    // ---- settings: frame and call ----
    "Connecting to the engine": "جارٍ الاتصال بالمحرك",
    "Connected": "متصل",
    "In a call": "في مكالمة",
    "Engine offline, retrying": "المحرك غير متصل، جارٍ إعادة المحاولة",
    "Not connected to the engine": "غير متصل بالمحرك",
    "Call and answers": "المكالمة والإجابات",
    "Roles": "الأدوار",
    "Model and speed": "النموذج والسرعة",
    "Documents": "المستندات",
    "API keys": "مفاتيح API",
    "Hotkeys": "اختصارات لوحة المفاتيح",
    "Display": "العرض",
    "Usage": "الاستهلاك",
    "Saved": "تم الحفظ",
    "Saved. Applies from the next call.": "تم الحفظ. يُطبَّق من المكالمة التالية.",
    "Saved. The next question uses it.": "تم الحفظ. السؤال التالي سيستخدمه.",
    "The kind of call picks the prompt and the roles; the profile decides how the lines look. The profile applies from the next question, even during a call.": "نوع المكالمة يحدد التعليمات والأدوار، ونمط الإجابة يحدد شكل السطور. النمط يُطبَّق من السؤال التالي، حتى أثناء المكالمة.",
    "Profile": "نمط الإجابة",
    "Direction: short moves to make": "توجيه: خطوات قصيرة",
    "Choose under Roles": "اختره من الأدوار",
    "The role applies from the next call.": "الدور يُطبَّق من المكالمة التالية.",

    // ---- settings: roles ----
    "A role tells the model who is speaking and what a strong answer from them brings out. Search or browse the roles for this kind of call and click one to choose it, or describe your own.": "الدور يخبر النموذج مَن المتحدث وما الذي تُبرزه الإجابة القوية منه. ابحث في أدوار هذا النوع من المكالمات أو تصفحها واضغط على دور لاختياره، أو صف دورك.",
    "Roles for interviews": "أدوار مقابلات العمل",
    // ---- meeting mode (roadmap item 18) ----
    "Meeting": "اجتماع",
    "Roles for meetings": "أدوار الاجتماعات",
    "Meeting material": "مواد الاجتماع",
    "No document. Suggestions will not state project facts or figures.": "لا يوجد مستند. لن تذكر الاقتراحات حقائق أو أرقامًا عن المشروع.",
    "No meeting material loaded. The model cannot state project facts, figures or dates.": "لم تُحمَّل مواد الاجتماع. لا يستطيع النموذج ذكر حقائق المشروع أو أرقامه أو مواعيده.",
    "Start without meeting material": "ابدأ بدون مواد الاجتماع",
    "No material": "بلا مواد",
    "Search roles: nurse, data, sales, engineer": "ابحث في الأدوار بالإنجليزية: nurse، data، sales، engineer",
    "No role matches. Describe it below and it is added.": "لا يوجد دور مطابق. صفه بالأسفل وسيُضاف.",
    "Your roles": "أدوارك",
    "General": "عام",
    "Software and IT": "البرمجيات وتقنية المعلومات",
    "Data and AI": "البيانات والذكاء الاصطناعي",
    "Design and creative": "التصميم والإبداع",
    "Product and projects": "المنتجات والمشروعات",
    "Business, finance and law": "الأعمال والمالية والقانون",
    "Marketing and communication": "التسويق والاتصال",
    "Sales and customer service": "المبيعات وخدمة العملاء",
    "Operations and management": "العمليات والإدارة",
    "People and HR": "الموارد البشرية",
    "Healthcare": "الرعاية الصحية",
    "Education": "التعليم",
    "Engineering": "الهندسة",
    "Hospitality and travel": "الضيافة والسفر",
    "Technology and telecom": "التكنولوجيا والاتصالات",
    "Property and finance": "العقارات والتمويل",
    "Consumer": "المستهلكون",
    "Business services": "خدمات الأعمال",
    "Accounts and partners": "الحسابات والشركاء",
    "yours": "خاص بك",
    "Describe your role": "صف دورك",
    "Say what you do, where, and what your work is judged on. Type it, or press Speak and say it; you can edit the text before it is analyzed. The model writes it up as a role and it is added to the list above.": "قل ماذا تعمل، وأين، وعلى أي أساس يُقيَّم عملك. اكتبه، أو اضغط تحدث وقُله؛ يمكنك تعديل النص قبل تحليله. النموذج يكتبه كدور ويُضاف إلى القائمة أعلاه.",
    "For example: I am a pharmacy technician in a hospital. I prepare and check prescriptions, manage stock, and work with the pharmacists on the wards.": "مثال: أعمل فني صيدلة في مستشفى. أجهز الوصفات وأراجعها، وأدير المخزون، وأعمل مع الصيادلة في الأقسام.",
    "Speak": "تحدث",
    "Stop": "إيقاف",
    "Analyze and add": "حلّل وأضف",
    "Say a little more about the role first: at least five words.": "قل المزيد عن الدور أولًا: خمس كلمات على الأقل.",
    "The model is writing the role up": "النموذج يكتب الدور",
    "Starting the microphone": "جارٍ تشغيل الميكروفون",
    "Listening: say what you do. Press Stop when you are done (it stops by itself after a minute).": "يستمع: قل ماذا تعمل. اضغط إيقاف عند الانتهاء (يتوقف تلقائيًا بعد دقيقة).",
    "Check the text, then press Analyze and add.": "راجع النص، ثم اضغط حلّل وأضف.",
    "Nothing was heard.": "لم يُسمع شيء.",
    "The microphone stopped.": "توقف الميكروفون.",
    "Role added and chosen": "أُضيف الدور واختير",

    // ---- settings: model and speed ----
    "The model writes the lines. The delay you feel is mostly the provider, and it changes by the hour: test them from here and use the fastest.": "النموذج يكتب السطور. التأخير الذي تشعر به سببه غالبًا المزوّد، ويتغير من ساعة لأخرى: اختبرهم من هنا واستخدم الأسرع.",
    "Provider": "المزوّد",
    "Model id (optional)": "معرّف النموذج (اختياري)",
    "the provider's default": "الافتراضي لدى المزوّد",
    "Ask the model early: faster, but more requests": "اسأل النموذج مبكرًا: أسرع، لكن بطلبات أكثر",
    "During a call a new provider takes the next question. Asking early applies from the next call.": "أثناء المكالمة يتولى المزوّد الجديد السؤال التالي. السؤال المبكر يُطبَّق من المكالمة التالية.",
    "No model has a key yet: add one under API keys": "لا يوجد نموذج له مفتاح بعد: أضف مفتاحًا في مفاتيح API",
    "Switching the model for the next question": "جارٍ تغيير النموذج للسؤال التالي",
    "Speed test": "اختبار السرعة",
    "Sends the same short question to every model with a key, twice each, one after another, and times the first line and the whole answer. It runs between calls and counts toward usage.": "يرسل نفس السؤال القصير إلى كل نموذج له مفتاح، مرتين لكل نموذج، واحدًا تلو الآخر، ويقيس زمن أول سطر والإجابة كاملة. يعمل بين المكالمات ويُحتسب في الاستهلاك.",
    "Test speed": "اختبر السرعة",
    "Use the fastest": "استخدم الأسرع",
    "First line": "أول سطر",
    "Whole answer": "الإجابة كاملة",
    "Result": "النتيجة",
    "waiting": "في الانتظار",
    "no usable lines": "لا توجد سطور صالحة",
    "No model answered with usable lines.": "لم يُجب أي نموذج بسطور صالحة.",

    // ---- settings: documents and listening ----
    "These are the documents for the kind of call chosen under Call and answers (text, Markdown, PDF or Word). Added during a call of that kind, they are used from the next question. A document that would take them past the word limit is refused, not cut.": "هذه مستندات نوع المكالمة المختار في المكالمة والإجابات (نص أو Markdown أو PDF أو Word). ما يُضاف أثناء مكالمة من هذا النوع يُستخدم من السؤال التالي. المستند الذي يتجاوز بها حد الكلمات يُرفض ولا يُقتطع.",
    "No document loaded.": "لم يُحمَّل أي مستند.",
    "Fawa2ly hears the call through the speakers or headset it plays on. These apply from the next call.": "فوقلي يسمع المكالمة عبر السماعات أو سماعة الرأس التي تعمل عليها. تُطبَّق هذه الإعدادات من المكالمة التالية.",
    "For a call in Arabic choose Arabic: Auto does not include it, and the lines come back in the language chosen here.": "لمكالمة بالعربية اختر العربية: التلقائي لا يشملها، والسطور تأتي باللغة المختارة هنا.",
    "The call's audio": "صوت المكالمة",
    "Your microphone": "الميكروفون الخاص بك",
    "Windows default speakers": "سماعات Windows الافتراضية",
    "Windows default microphone": "ميكروفون Windows الافتراضي",
    "Pick the device your meeting app uses if it is not the Windows default. A device unplugged later falls back to the default.": "اختر الجهاز الذي يستخدمه تطبيق الاجتماعات إن لم يكن الافتراضي في Windows. الجهاز الذي يُفصل لاحقًا يعود إلى الافتراضي.",

    // ---- settings: keys and hotkeys ----
    "This app uses your own keys: Deepgram to listen, and one model provider to write the lines. They are saved only on this computer, in %APPDATA%\\Fawa2ly\\.env, and this window never shows a saved key again.": "هذا التطبيق يستخدم مفاتيحك: Deepgram للاستماع، ومزوّد نموذج واحد لكتابة السطور. تُحفظ على هذا الكمبيوتر فقط، في %APPDATA%\\Fawa2ly\\.env، ولا تعرض هذه النافذة أي مفتاح محفوظ مرة أخرى.",
    "Save keys": "حفظ المفاتيح",
    // ---- settings: API keys and providers (2026-10-16) ----
    "API keys and providers": "مفاتيح API والمزوّدون",
    "This app uses your own keys: Deepgram to listen, and a model provider to write the lines. Keys are saved only on this computer, in %APPDATA%\\Fawa2ly\\.env, and this window never shows a saved key again.": "هذا التطبيق يستخدم مفاتيحك: Deepgram للاستماع، ومزوّد نموذج لكتابة السطور. تُحفظ المفاتيح على هذا الكمبيوتر فقط، في %APPDATA%\\Fawa2ly\\.env، ولا تعرض هذه النافذة أي مفتاح محفوظ مرة أخرى.",
    "Save key": "حفظ المفتاح",
    "Not needed if Listening is set to transcribe on this computer.": "غير مطلوب إذا كان الاستماع مضبوطًا على التفريغ على هذا الكمبيوتر.",
    "On this computer": "على هذا الكمبيوتر",
    "Scan for local servers": "البحث عن خوادم محلية",
    "A model server running here: nothing you say leaves this computer and no key is needed. A server that answers the scan is turned on.": "خادم نموذج يعمل هنا: لا يغادر شيء مما تقوله هذا الكمبيوتر ولا حاجة لمفتاح. أي خادم يستجيب للبحث يتم تفعيله.",
    "Cloud providers": "مزوّدون سحابيون",
    "Open a provider to add its key, then fetch its models and pick one. Only providers marked Ready appear in the model list.": "افتح مزوّدًا لإضافة مفتاحه، ثم اجلب نماذجه واختر واحدًا. تظهر في قائمة النماذج المزوّدات الجاهزة فقط.",
    "Your own endpoints": "نقاط الاتصال الخاصة بك",
    "Any server that speaks the OpenAI chat completions API: a company gateway, LiteLLM, a proxy, or a model server on another computer.": "أي خادم يدعم واجهة OpenAI للمحادثة: بوابة شركة، أو LiteLLM، أو وسيط، أو خادم نموذج على كمبيوتر آخر.",
    "Add an endpoint": "إضافة نقطة اتصال",
    "Name": "الاسم",
    "My gateway": "بوابتي",
    "Address": "العنوان",
    "Model id": "معرّف النموذج",
    "Can be fetched after adding": "يمكن جلبه بعد الإضافة",
    "Key (optional)": "المفتاح (اختياري)",
    "Add endpoint": "إضافة نقطة الاتصال",
    "Offer this provider in the model list": "إظهار هذا المزوّد في قائمة النماذج",
    "Key": "المفتاح",
    "Temperature": "درجة العشوائية",
    "Models from the provider": "نماذج المزوّد",
    "Test the connection to list them": "اختبر الاتصال لعرضها",
    "Save": "حفظ",
    "Test connection": "اختبار الاتصال",
    "Remove key": "حذف المفتاح",
    "Remove endpoint": "حذف نقطة الاتصال",
    "Off": "متوقف",
    "Needs a key": "يحتاج مفتاحًا",
    "Pick a model": "اختر نموذجًا",
    "Ready": "جاهز",
    "Optional": "اختياري",
    "Provider default": "افتراضي المزوّد",
    "Pick or type a model id": "اختر معرّف نموذج أو اكتبه",
    "Set. Paste a new key to replace it": "محفوظ. الصق مفتاحًا جديدًا لاستبداله",
    "Checking...": "جارٍ الفحص...",
    "Scanning...": "جارٍ البحث...",
    "Nothing changed": "لم يتغير شيء",
    "Key removed": "تم حذف المفتاح",
    "The provider listed no models": "لم يعرض المزوّد أي نماذج",
    "Give the endpoint a name and an address": "أعطِ نقطة الاتصال اسمًا وعنوانًا",
    "Endpoint added. Open it to test the connection.": "تمت إضافة نقطة الاتصال. افتحها لاختبار الاتصال.",
    "No local model server answered. Start one (for example Ollama or LM Studio), then scan again.": "لم يستجب أي خادم نموذج محلي. شغّل واحدًا (مثل Ollama أو LM Studio) ثم ابحث مرة أخرى.",
    "Leave a box empty to keep a key as it is.": "اترك الخانة فارغة للإبقاء على المفتاح كما هو.",
    "Paste a new key to replace it": "الصق مفتاحًا جديدًا لاستبداله",
    "Paste the key": "الصق المفتاح",
    "Paste a key first": "الصق مفتاحًا أولًا",
    "Keys saved on this computer. They apply from the next call.": "حُفظت المفاتيح على هذا الكمبيوتر. تُطبَّق من المكالمة التالية.",
    "These work from any application, so they take the combination away from it while Fawa2ly runs.": "تعمل من أي تطبيق، لذلك تأخذ الاختصار منه طوال عمل فوقلي.",
    "Click a box and press the new combination: at least one of Ctrl, Alt or Shift, and a key. Backspace clears one; show and hide cannot be cleared. A combination Fawa2ly already uses runs its action instead of being recorded.": "اضغط على خانة ثم اضغط الاختصار الجديد: واحد على الأقل من Ctrl أو Alt أو Shift، ومفتاح. Backspace يمسح الاختصار؛ الإظهار والإخفاء لا يمكن مسحه. الاختصار الذي يستخدمه فوقلي بالفعل يُنفَّذ إجراؤه بدل تسجيله.",
    "Show and hide": "الإظهار والإخفاء",
    "Click-through": "تمرير النقرات",
    "Pause and resume": "الإيقاف المؤقت والاستئناف",
    "Previous question": "السؤال السابق",
    "Next question": "السؤال التالي",
    "More lines": "سطور إضافية",
    "Press the keys": "اضغط المفاتيح",
    "Not set": "غير محدد",
    "Cleared": "تم المسح",
    "Add Ctrl, Alt or Shift: a key on its own would stop typing in every application": "أضف Ctrl أو Alt أو Shift: المفتاح وحده سيمنع الكتابة في كل التطبيقات",

    // ---- settings: display and usage ----
    "How the app looks, and its language. Changes show at once.": "شكل التطبيق ولغته. التغييرات تظهر فورًا.",
    "App language": "لغة التطبيق",
    "Both windows and every theme switch at once, and Arabic is laid out right to left. The language a call is in is chosen under Listening.": "النافذتان وكل المظاهر تتحول فورًا، والعربية تُعرض من اليمين إلى اليسار. لغة المكالمة نفسها تُختار من الاستماع.",
    "Theme": "المظهر",
    "Dark (default)": "داكن (افتراضي)",
    "Light": "فاتح",
    "Fawa2ly (default)": "فوقلي (افتراضي)",
    "Daylight": "نهاري",
    "During calls": "أثناء المكالمة",
    "You and the job": "أنت والوظيفة",
    "Past calls": "المكالمات السابقة",
    "Model, keys, usage": "النموذج والمفاتيح والاستهلاك",
    "Look and hotkeys": "الشكل والاختصارات",
    // updates (release 0.1.0)
    "Look, hotkeys, updates": "الشكل والاختصارات والتحديثات",
    "A new version is ready": "يوجد إصدار جديد",
    "Update and restart": "حدّث وأعد التشغيل",
    "Later": "لاحقًا",
    "Updates": "التحديثات",
    "Fawa2ly looks for a new version when it starts and every few hours. Nothing is downloaded until you press Update, and every update is checked against the project's signature before it runs.": "يبحث فوقلي عن إصدار جديد عند تشغيله وكل بضع ساعات. لا يُنزَّل أي شيء حتى تضغط تحديث، ويُتحقق من توقيع المشروع على كل تحديث قبل تشغيله.",
    "This version": "هذا الإصدار",
    "Check for updates": "ابحث عن تحديثات",
    "What's new": "الجديد",
    "Updates are installed by the desktop app; this page is open in a browser.": "التحديثات يثبّتها تطبيق سطح المكتب؛ هذه الصفحة مفتوحة في متصفح.",
    "Checked when Fawa2ly starts and every few hours.": "يتم البحث عند تشغيل فوقلي وكل بضع ساعات.",
    "Checking for updates": "جارٍ البحث عن تحديثات",
    "You have the newest version.": "لديك أحدث إصدار.",
    "The update did not install": "لم يكتمل تثبيت التحديث",
    "Downloading": "جارٍ التنزيل",
    "Downloading the update": "جارٍ تنزيل التحديث",
    "Installing. Fawa2ly closes and opens again by itself.": "جارٍ التثبيت. سيُغلق فوقلي ثم يُفتح من جديد تلقائيًا.",
    "Aurora": "أورورا",
    "Teleprompter": "ملقّن",
    "Paper": "ورق",
    "Terminal": "طرفية",
    "Frost": "صقيع",
    "High contrast": "تباين عالٍ",
    "Text size": "حجم النص",
    "Small": "صغير",
    "Medium": "متوسط",
    "Large": "كبير",
    "Type text in as it arrives": "اكتب النص تدريجيًا عند وصوله",
    "Background opacity": "شفافية الخلفية",
    "Only the background fades; the text stays solid.": "الخلفية فقط هي التي تبهت؛ النص يبقى واضحًا.",
    "Tokens sent to and received from the models by this app: the answers, the early requests, More, the speed test and role analysis. Some providers do not report tokens for a streamed answer; those requests are counted as not reported rather than as zero.": "التوكنز المرسلة إلى النماذج والمستلمة منها عبر هذا التطبيق: الإجابات، والطلبات المبكرة، والمزيد، واختبار السرعة، وتحليل الأدوار. بعض المزوّدين لا يبلّغون عن توكنز الإجابة المتدفقة؛ تُحتسب هذه الطلبات غير مُبلَّغ عنها وليست صفرًا.",
    "After the call": "بعد المكالمة",
    "Save a transcript of each call on this computer": "احفظ نص كل مكالمة على هذا الكمبيوتر",
    "Off unless you turn it on. Each call becomes one file: what the other side said, what you said when your microphone is transcribed, the questions and the lines shown. Never the audio. The summary at the end of a call does not need it. While it is on, the call's diagnostic logs (including the raw speech-to-text output) are kept in %APPDATA%\\Fawa2ly\\sessions too; while it is off, nothing about a call is written.": "مغلق ما لم تفعّله. كل مكالمة تُحفظ في ملف واحد: ما قاله الطرف الآخر، وما قلته أنت عندما يُحوَّل كلام الميكروفون إلى نص، والأسئلة والسطور التي ظهرت. لا يُحفظ الصوت أبدًا. ملخص نهاية المكالمة لا يحتاج إليه. وأثناء تفعيله تُحفظ أيضًا سجلات التشخيص للمكالمة (ومنها مخرجات تحويل الكلام إلى نص الخام) في %APPDATA%\\Fawa2ly\\sessions؛ وعند إيقافه لا يُكتب شيء عن المكالمة.",
    "None saved.": "لا يوجد نص محفوظ.",
    "Delete saved transcripts": "حذف النصوص المحفوظة",
    "Click again to delete them": "اضغط مرة أخرى لحذفها",
    "Transcripts deleted": "تم حذف النصوص",
    "Transcript not saved. To keep calls, turn it on in Settings, under Call and answers.": "لم يُحفظ نص المكالمة. لحفظ المكالمات، فعّله من الإعدادات في المكالمة والإجابات.",
    "Calls": "المكالمات",
    "Calls saved while transcript saving was on, newest first. A review asks the saved model to read what was said and judge each answer; it counts toward usage and is kept with the call.": "المكالمات المحفوظة أثناء تفعيل حفظ النص، الأحدث أولًا. المراجعة تطلب من النموذج المحفوظ قراءة ما قيل والحكم على كل إجابة؛ تُحتسب في الاستهلاك وتُحفظ مع المكالمة.",
    "Negotiation help: this call is about pay or price": "مساعدة التفاوض: هذه المكالمة عن الأجر أو السعر",
    "For a call where money comes up. The lines cover anchors, counters and what to trade when the number cannot move, and they leave the deciding to you. Figures come only from your documents or from what was said on the call: nothing is a going rate the model made up, and nothing has you claim an offer or a competing price you do not have. Turn it off for ordinary calls. Applies from the next call.": "لمكالمة يُذكر فيها المال. تغطي السطور نقطة البداية والردود وما يمكن مقايضته حين لا يتحرك الرقم، وتترك لك القرار. الأرقام تأتي فقط من مستنداتك أو مما قيل في المكالمة: لا يوجد سعر سائد اختلقه النموذج، ولا شيء يجعلك تدّعي عرضًا أو سعرًا منافسًا ليس لديك. أوقفه في المكالمات العادية. يسري من المكالمة التالية.",
    "Job description": "الوصف الوظيفي",
    "Paste the job description for an interview you are preparing for. The model compares it with your CV and says what matches, what is missing and what to have ready. It is kept as an interview document too, so the call knows what the job asks for. One request, between calls, counted toward usage.": "الصق الوصف الوظيفي للمقابلة التي تستعد لها. يقارنه النموذج بسيرتك الذاتية ويوضح ما يتطابق وما ينقص وما ينبغي تجهيزه. ويُحفظ أيضًا كمستند للمقابلة، لتعرف المكالمة ما تطلبه الوظيفة. طلب واحد، بين المكالمات، ويُحتسب ضمن الاستخدام.",
    "Paste the whole job description here: responsibilities, requirements and anything about the team.": "الصق الوصف الوظيفي كاملًا هنا: المسؤوليات والمتطلبات وأي شيء عن الفريق.",
    "Compare with my CV": "قارنه بسيرتي الذاتية",
    "Remove it": "إزالته",
    "What matches": "ما يتطابق",
    "What is missing": "ما ينقص",
    "What to prepare": "ما ينبغي تجهيزه",
    "The model is comparing it with your CV": "النموذج يقارنه بسيرتك الذاتية",
    "Kept with your documents, and used in the next interview.": "محفوظ مع مستنداتك، وسيُستخدم في المقابلة التالية.",
    "Paste the job description itself: at least 25 words.": "الصق الوصف الوظيفي نفسه: 25 كلمة على الأقل.",
    "Removed.": "تمت الإزالة.",
    "The job description is no longer one of your documents, so the next interview will not see it. Paste it again to put it back.": "لم يعد الوصف الوظيفي ضمن مستنداتك، لذا لن تراه المقابلة التالية. الصقه مرة أخرى لإعادته.",
    "Started": "البداية",
    "Length": "المدة",
    "Questions": "الأسئلة",
    "Review": "المراجعة",
    "No saved calls. Turn on transcript saving under Call and answers, and calls are listed here.": "لا توجد مكالمات محفوظة. فعّل حفظ النص من المكالمة والإجابات، وستظهر المكالمات هنا.",
    "Review this call": "راجع هذه المكالمة",
    "Search what was said on every saved call": "ابحث فيما قيل في كل المكالمات المحفوظة",
    "Searching": "جارٍ البحث",
    "Nothing found.": "لم يُعثر على شيء.",
    "Write notes": "اكتب الملاحظات",
    "Write notes again": "اكتب الملاحظات مرة أخرى",
    "Notes": "الملاحظات",
    "Decisions": "القرارات",
    "Action items": "المهام",
    "Open questions": "أسئلة مفتوحة",
    "Copy as Markdown": "انسخ بصيغة Markdown",
    "Copied": "تم النسخ",
    "Could not copy": "تعذر النسخ",
    "The model is writing the notes": "النموذج يكتب الملاحظات",
    "Review again": "راجعها مرة أخرى",
    "out of 10": "من 10",
    "Went well": "ما نجح",
    "Next time": "في المرة القادمة",
    "Reviewed": "تمت المراجعة",
    "Not yet": "لم تُراجع بعد",
    "None of your words": "بلا كلام منك",
    "Landed": "أصابت",
    "Partly": "جزئيًا",
    "Missed": "فاتت",
    "The model is reading the call": "النموذج يقرأ المكالمة",
    "Nothing to review: your microphone was not transcribed on this call.": "لا يوجد ما يُراجع: لم يُحوَّل كلام الميكروفون إلى نص في هذه المكالمة.",
    "Your speaking pace over your last 40 words, and filler words so far": "سرعة كلامك في آخر 40 كلمة، وكلمات الحشو حتى الآن",
    "Show my speaking pace while I talk": "اعرض سرعة كلامي أثناء حديثي",
    "Needs your microphone transcribed, under Listening. Words per minute over your last 40 words; filler words such as uh and um are counted on English calls only.": "يحتاج تحويل كلام الميكروفون إلى نص، من الاستماع. عدد الكلمات في الدقيقة على آخر 40 كلمة؛ كلمات الحشو مثل uh وum تُحسب في المكالمات الإنجليزية فقط.",
    "During the call": "أثناء المكالمة",
    "Live feedback on my answers": "ملاحظات فورية على إجاباتي",
    "Needs your microphone transcribed, under Listening. When the other side speaks again after one of your answers, the saved model says whether it was on point, missed the point, ran long or drifted, with a short note. One extra request per answer, at most 20 a call, sent only while no answer is being written. Applies from the next call.": "يحتاج تحويل كلام الميكروفون إلى نص، من الاستماع. عندما يتكلم الطرف الآخر بعد إحدى إجاباتك، يقول النموذج المحفوظ هل كانت في الصميم أو لم تُصب الهدف أو أطلت أو خرجت عن الموضوع، مع ملاحظة قصيرة. طلب إضافي واحد لكل إجابة، 20 على الأكثر في المكالمة، ويُرسل فقط عندما لا تُكتب إجابة. يُطبَّق من المكالمة التالية.",
    "On point": "في الصميم",
    "Missed the point": "لم تُصب الهدف",
    "Ran long": "أطلت",
    "Drifted": "خرجت عن الموضوع",
    // ---- actions on demand (roadmap item 17) ----
    "What should I say?": "ماذا أقول؟",
    "Lines to say next, without waiting for a question": "سطور تقولها الآن، دون انتظار سؤال",
    "Recap": "ملخص",
    "The conversation so far, to catch up": "المحادثة حتى الآن، لتلحق بها",
    "Questions to ask": "أسئلة تطرحها",
    "Questions you could ask the other side": "أسئلة يمكنك طرحها على الطرف الآخر",
    "Shorter": "أقصر",
    "The same answer in fewer, shorter lines": "نفس الإجابة في سطور أقل وأقصر",
    "Today": "اليوم",
    "This month": "هذا الشهر",
    "All time": "منذ البداية",
    "By model, all time": "حسب النموذج، منذ البداية",
    "Requests": "الطلبات",
    "Input tokens": "توكنز الإدخال",
    "Output tokens": "توكنز الإخراج",
    "Not reported": "غير مُبلَّغ عنها",
    "Nothing counted yet.": "لم يُحتسب شيء بعد.",
    "Refresh": "تحديث",
    "Reset the count": "تصفير العداد",
    "Click again to reset": "اضغط مرة أخرى للتصفير",
    "Usage count reset": "تم تصفير عداد الاستهلاك",
  };

  const PATTERNS = [
    [/^Fawa2ly (\S+) is ready$/, (m) => `فوقلي ${m[1]} جاهز`],
    [/^Version (\S+)$/, (m) => `الإصدار ${m[1]}`],
    [/^You have (\S+)\. Updating takes a minute and reopens the app\.$/, (m) => `لديك الإصدار ${m[1]}. يستغرق التحديث دقيقة ثم يُعاد فتح التطبيق.`],
    [/^Downloading, (\d+)%$/, (m) => `جارٍ التنزيل، ${m[1]}%`],
    [/^Downloading the update, (\d+)%$/, (m) => `جارٍ تنزيل التحديث، ${m[1]}%`],
    [/^Could not check for updates: (.+)$/, (m) => `تعذر البحث عن تحديثات: ${m[1]}`],
    [/^The update did not install: (.+)$/, (m) => `لم يكتمل تثبيت التحديث: ${m[1]}`],
    [/^([\d,.]+) of ([\d,.]+) words\. (Suggestions|Answers) will use (it|them)\.$/,
     (m) => `${m[1]} من ${m[2]} كلمة. ${m[3] === "Suggestions" ? "الاقتراحات" : "الإجابات"} ${m[4] === "it" ? "ستستخدمه" : "ستستخدمها"}.`],
    [/^([\d,.]+) words$/, (m) => `${m[1]} كلمة`],
    [/^Remove (.+)$/, (m) => `إزالة ${m[1]}`],
    [/^Delete (.+)$/, (m) => `حذف ${m[1]}`],
    [/^Call ended, (\d+) questions?$/, (m) => `انتهت المكالمة، عدد الأسئلة: ${m[1]}`],
    [/^Everything said and suggested is in (.+)\.$/, (m) => `كل ما قيل واقتُرح محفوظ في ${m[1]}.`],
    [/^switching to (.+)$/, (m) => `جارٍ التحويل إلى ${m[1]}`],
    [/^In a call on (.+)$/, (m) => `في مكالمة على ${m[1]}`],
    [/^Testing (\d+) models?, one after another$/, (m) => `جارٍ اختبار النماذج (${m[1]})، واحدًا تلو الآخر`],
    [/^The test stopped: (.+)$/, (m) => `توقف الاختبار: ${m[1]}`],
    [/^Fastest: (.+), first line in ([\d.]+) s\.$/, (m) => `الأسرع: ${m[1]}، أول سطر خلال ${m[2]} ث.`],
    [/^Now using (.+)$/, (m) => `يُستخدم الآن ${m[1]}`],
    [/^Use (.+)$/, (m) => `استخدم ${m[1]}`],
    [/^(\d+) lines?$/, (m) => `السطور: ${m[1]}`],
    [/^([\d.]+) s$/, (m) => `${m[1]} ث`],
    [/^([\d,.]+) in, ([\d,.]+) out, ([\d,.]+) requests?(?:, ([\d,.]+) not reported)?$/,
     (m) => `${m[1]} إدخال، ${m[2]} إخراج، ${m[3]} طلب` + (m[4] ? `، ${m[4]} غير مُبلَّغ عنها` : "")],
    [/^Counted since (.+)\.$/, (m) => `محتسب منذ ${m[1]}.`],
    [/^Added "(.+)" and chose it for (interviews|meetings)\.$/,
     (m) => `أُضيف "${m[1]}" واختير ${{ meetings: "للاجتماعات" }[m[2]] || "لمقابلات العمل"}.`],
    [/^Reading (.+)$/, (m) => `جارٍ قراءة ${m[1]}`],
    [/^(.+) is larger than (\d+) MB: use a smaller file$/, (m) => `حجم ${m[1]} أكبر من ${m[2]} ميغابايت: استخدم ملفًا أصغر`],
    [/^(.+) \(not connected\)$/, (m) => `${m[1]} (غير متصل)`],
    [/^Deepgram, to listen: (set|not set)$/, (m) => `Deepgram، للاستماع: ${m[1] === "set" ? "محفوظ" : "غير محفوظ"}`],
    [/^(.+) model: (set|not set)$/, (m) => `نموذج ${m[1]}: ${m[2] === "set" ? "محفوظ" : "غير محفوظ"}`],
    [/^(?:(\d+) min )?(\d+) s · (\d+) questions? heard · (\d+) answered(?: · first line in ([\d.]+) s, median)?(?: · you: (\d+) wpm)?(?: · (\d+) fillers?)?$/,
     (m) => `${m[1] ? `${m[1]} د ` : ""}${m[2]} ث · أسئلة مسموعة: ${m[3]} · أُجيب عنها: ${m[4]}`
       + (m[5] ? ` · أول سطر خلال ${m[5]} ث (الوسيط)` : "")
       + (m[6] ? ` · سرعتك: ${m[6]} كلمة/د` : "") + (m[7] ? ` · كلمات حشو: ${m[7]}` : "")],
    [/^(\d+) wpm · (\d+) fillers?$/, (m) => `${m[1]} كلمة/د · حشو: ${m[2]}`],
    [/^(\d+) wpm$/, (m) => `${m[1]} كلمة/د`],
    [/^Transcript saved in (.+)\.$/, (m) => `حُفظ نص المكالمة في ${m[1]}.`],
    [/^Transcript not saved: (.+)$/, (m) => `لم يُحفظ نص المكالمة: ${m[1]}`],
    [/^(\d+) saved in (.+)$/, (m) => `محفوظ: ${m[1]}، في ${m[2]}`],
    [/^(\d+) min (\d+) s$/, (m) => `${m[1]} د ${m[2]} ث`],
    [/^(\d+) of (\d+) answered$/, (m) => `أُجيب عن ${m[1]} من ${m[2]}`],
    [/^Not active: (.+)$/, (m) => `غير مفعّل: ${m[1]}`],
  ];

  const ATTRS = ["placeholder", "title", "aria-label"];
  let lang = "en";
  try { lang = localStorage.getItem("ui-lang") === "ar" ? "ar" : "en"; } catch (e) { /* no storage */ }

  function translate(text) {
    if (lang !== "ar" || !text) return text;
    const core = text.trim();
    if (!core) return text;
    let out = Object.prototype.hasOwnProperty.call(AR, core) ? AR[core] : undefined;
    if (out === undefined) {
      for (const [re, make] of PATTERNS) {
        const m = core.match(re);
        if (m) { out = make(m); break; }
      }
    }
    if (out === undefined) return text;
    const at = text.indexOf(core);
    return text.slice(0, at) + out + text.slice(at + core.length);   // keep the spacing around it
  }

  // Each node remembers the English it came with and what was put in its place, so a change the
  // page makes is told apart from this file's own, and English can be restored.
  const texts = new WeakMap();
  const attrs = new WeakMap();
  const excluded = (el) => !el || Boolean(el.closest('[translate="no"], script, style'));

  function doText(node) {
    if (excluded(node.parentElement)) return;
    const seen = texts.get(node);
    const source = seen && node.nodeValue === seen.shown ? seen.source : node.nodeValue;
    const shown = translate(source);
    texts.set(node, { source, shown });
    if (node.nodeValue !== shown) node.nodeValue = shown;
  }

  function doAttrs(el) {
    if (excluded(el)) return;
    let seen = attrs.get(el);
    for (const name of ATTRS) {
      if (!el.hasAttribute(name)) continue;
      const value = el.getAttribute(name);
      const before = seen && seen[name];
      const source = before && value === before.shown ? before.source : value;
      const shown = translate(source);
      if (!seen) attrs.set(el, (seen = {}));
      seen[name] = { source, shown };
      if (value !== shown) el.setAttribute(name, shown);
    }
  }

  function walk(root) {
    if (root.nodeType === Node.TEXT_NODE) return doText(root);
    if (root.nodeType !== Node.ELEMENT_NODE) return;
    doAttrs(root);
    const it = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    for (let n = it.nextNode(); n; n = it.nextNode()) {
      if (n.nodeType === Node.TEXT_NODE) doText(n);
      else doAttrs(n);
    }
  }

  function apply() {
    const root = document.documentElement;
    root.lang = lang;
    root.dir = lang === "ar" ? "rtl" : "ltr";
    walk(root);
  }

  function setLang(next) {
    next = next === "ar" ? "ar" : "en";
    if (next === lang) return;
    lang = next;
    try { localStorage.setItem("ui-lang", lang); } catch (e) { /* no storage */ }
    apply();
  }

  new MutationObserver((records) => {
    for (const r of records) {
      if (r.type === "characterData") doText(r.target);
      else if (r.type === "attributes") doAttrs(r.target);
      else for (const n of r.addedNodes) walk(n);
    }
  }).observe(document.documentElement, { subtree: true, childList: true, characterData: true,
                                          attributes: true, attributeFilter: ATTRS });
  apply();

  window.fawa2lyI18n = { setLang, translate, get lang() { return lang; } };
})();
