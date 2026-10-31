// UI strings in English and Hindi. Other languages keep the English UI while the AI answers,
// listens and speaks in the chosen language.

const STRINGS = {
  en: {
    tagline: 'Climate-risk shield for every farm', language: 'Language', live_data: 'Live data',
    your_farm: 'Your farm', search_placeholder: 'Search village / town / district', use_location: '📍 Use my location',
    click_map: 'Tap the map to mark your field.', crop: 'Crop', sowing_date: 'Sowing / transplanting date',
    irrigation: 'Irrigation', irr_drip: 'Drip', irr_sprinkler: 'Sprinkler', irr_furrow: 'Furrow',
    irr_flood: 'Flood / basin', irr_rainfed: 'Rainfed (no irrigation)', last_irrigation: 'Last irrigated (optional)',
    analyse: 'Analyse my farm', analysing: 'Analysing live data…', tab_dashboard: 'Risk dashboard',
    tab_chat: 'Ask the AI agronomist', tab_sentinel: 'Sentinel alerts',
    empty_title: 'Mark your field and press “Analyse my farm”.',
    empty_body: 'KhetKavach pulls live forecasts, 30-year climate normals, soil maps, river flood models, satellite imagery, disaster alerts and mandi prices for your exact location, then turns them into actions.',
    ask_placeholder: 'Ask about your farm…', send: 'Send', read_aloud: 'Read answers aloud',
    trace_title: 'Agent trace', trace_sub: 'live reasoning, tool calls & verification',
    sentinel_title: 'Kavach Sentinel',
    sentinel_body: "Registered farms are re-analysed automatically every few hours. New risks become alerts in the farmer's language, optionally sent to Telegram.",
    farm_name: 'Farm name', telegram_id: 'Telegram chat ID (optional)', register: 'Register current farm',
    telegram_help: "To get alerts on Telegram, start a chat with this server's bot, then get your chat ID from @userinfobot.",
    registered_farms: 'Registered farms', scan_all: 'Scan all now', alerts_title: 'Alerts',
    footer: 'Live sources: Open-Meteo (ECMWF/GFS/ICON, ERA5, GloFAS, CAMS) · ISRIC SoilGrids · Copernicus Sentinel-2 · GDACS · Agmarknet/data.gov.in · OpenStreetMap. Advisory only: confirm with your local KVK.',
    need_location: 'First mark your farm on the map (tap the map, search a place or use your location).',
    level_low: 'Low', level_watch: 'Watch', level_moderate: 'Moderate', level_high: 'High', level_severe: 'Severe',
    level_info: 'Info', level_unknown: 'Unavailable', top_risks: 'Top risks', no_major_risks: 'No major risks detected',
    updated: 'updated', cached: 'cached', stale: 'stale (source down, last good data)', evidence: 'Evidence',
    verified: 'data figures verified against live sources', self_corrected: 'self-corrected after verification',
    unverified: 'could not be verified', mode_ai: 'AI agent', mode_rules: 'Rule-based (AI offline)',
    thinking: 'Reasoning', step: 'Step', no_farms: 'No farms registered yet.', no_alerts: 'No alerts yet. Run a scan.',
    scan: 'Scan', delete: 'Delete', scanning: 'Scanning…', registered: 'Farm registered for Sentinel monitoring.',
    new_alerts: 'new alert(s)', mic_unsupported: 'Voice input is not supported in this browser (try Chrome).',
    no_voice: 'No voice for this language is installed on this device.',
    q1: 'What should I do on my farm this week?', q2: 'Should I irrigate in the next 3 days?',
    q3: 'When is the best time to spray this week?', q4: 'What is the mandi price today and where should I sell?',
    q5: 'Is there any flood, cyclone or heat risk?', q6: 'How healthy is my crop from satellite?',
    q7: 'What do my latest field photos show?',
    field_size: 'Field size in acres (optional)', tab_diary: 'Field diary', diary_title: 'Weekly check-in',
    diary_body: 'One photo of your crop each week and a few taps. The AI compares it with last week and uses it to tailor every answer.',
    photo_hint: '📷 Take or choose a crop photo (stand 1–2 m away, in daylight)', q_leaf: 'Leaf colour', q_pests: 'Pests seen',
    leaf_green: 'Green', leaf_yellowing: 'Yellowing', leaf_brown: 'Brown spots', leaf_mixed: 'Mixed',
    pests_none: 'None', pests_few: 'A few', pests_many: 'Many', q_irrigated: 'Irrigated on', q_sprayed: 'Sprayed on',
    q_fertilised: 'Fertiliser on', q_note: 'Anything else you noticed?', diary_submit: 'Save check-in',
    diary_history: 'Your field diary', diary_saving: 'Saving and analysing the photo…', diary_saved: 'Check-in saved.',
    diary_need_input: 'Add a photo or answer at least one question.', diary_empty: 'No check-ins yet. Add this week’s crop photo.',
    diary_add: 'Add this week’s photo', diary_due: 'Next check-in due', diary_overdue: 'This week’s photo is due',
    ai_unavailable: 'Photo saved. AI analysis needs an AI key.', ai_failed: 'Photo saved. The AI could not analyse it this time.',
    stage_seen: 'Stage seen', stage_expected: 'Expected stage', canopy: 'Canopy cover', water_stress: 'Water stress',
    weeds: 'Weeds', change: 'Change since last photo', next_checks: 'Check in the field', photo_quality: 'Photo quality',
    delete_entry: 'Delete', irrigation_updated: 'Last irrigation date updated from your check-in.',
  },
  hi: {
    tagline: 'हर खेत के लिए जलवायु-जोखिम कवच', language: 'भाषा', live_data: 'लाइव डेटा',
    your_farm: 'आपका खेत', search_placeholder: 'गाँव / कस्बा / ज़िला खोजें', use_location: '📍 मेरी लोकेशन लें',
    click_map: 'अपना खेत चिह्नित करने के लिए नक्शे पर टैप करें।', crop: 'फसल', sowing_date: 'बुवाई / रोपाई की तारीख',
    irrigation: 'सिंचाई', irr_drip: 'ड्रिप', irr_sprinkler: 'स्प्रिंकलर', irr_furrow: 'नाली (फरो)',
    irr_flood: 'बाढ़ / क्यारी', irr_rainfed: 'वर्षा आधारित (सिंचाई नहीं)', last_irrigation: 'पिछली सिंचाई (वैकल्पिक)',
    analyse: 'मेरे खेत का विश्लेषण करें', analysing: 'लाइव डेटा का विश्लेषण…', tab_dashboard: 'जोखिम डैशबोर्ड',
    tab_chat: 'AI कृषि विशेषज्ञ से पूछें', tab_sentinel: 'सेंटिनल अलर्ट',
    empty_title: 'अपना खेत चिह्नित करें और “विश्लेषण करें” दबाएँ।',
    empty_body: 'खेत कवच आपके खेत के लिए लाइव मौसम पूर्वानुमान, 30 साल के सामान्य आँकड़े, मिट्टी, नदी बाढ़ मॉडल, उपग्रह चित्र, आपदा चेतावनी और मंडी भाव जोड़कर काम की सलाह देता है।',
    ask_placeholder: 'अपने खेत के बारे में पूछें…', send: 'भेजें', read_aloud: 'जवाब बोलकर सुनाएँ',
    trace_title: 'एजेंट ट्रेस', trace_sub: 'लाइव तर्क, टूल कॉल और सत्यापन',
    sentinel_title: 'कवच सेंटिनल',
    sentinel_body: 'पंजीकृत खेतों का हर कुछ घंटों में अपने-आप विश्लेषण होता है। नए जोखिम किसान की भाषा में अलर्ट बनते हैं, चाहें तो Telegram पर भी।',
    farm_name: 'खेत का नाम', telegram_id: 'Telegram चैट ID (वैकल्पिक)', register: 'यह खेत पंजीकृत करें',
    telegram_help: 'Telegram पर अलर्ट के लिए इस सर्वर के बॉट से चैट शुरू करें, फिर @userinfobot से अपनी चैट ID लें।',
    registered_farms: 'पंजीकृत खेत', scan_all: 'अभी सब स्कैन करें', alerts_title: 'अलर्ट',
    footer: 'लाइव स्रोत: Open-Meteo (ECMWF/GFS/ICON, ERA5, GloFAS, CAMS) · ISRIC SoilGrids · Copernicus Sentinel-2 · GDACS · Agmarknet/data.gov.in · OpenStreetMap. केवल सलाह: अपने नज़दीकी KVK से पुष्टि करें।',
    need_location: 'पहले नक्शे पर अपना खेत चिह्नित करें (नक्शे पर टैप करें, जगह खोजें या लोकेशन लें)।',
    level_low: 'कम', level_watch: 'निगरानी', level_moderate: 'मध्यम', level_high: 'अधिक', level_severe: 'गंभीर',
    level_info: 'जानकारी', level_unknown: 'उपलब्ध नहीं', top_risks: 'मुख्य जोखिम', no_major_risks: 'कोई बड़ा जोखिम नहीं',
    updated: 'अपडेट', cached: 'कैश', stale: 'पुराना (स्रोत बंद, अंतिम सही डेटा)', evidence: 'साक्ष्य',
    verified: 'आँकड़े लाइव स्रोतों से सत्यापित', self_corrected: 'सत्यापन के बाद स्वयं सुधारा',
    unverified: 'सत्यापित नहीं हो सका', mode_ai: 'AI एजेंट', mode_rules: 'नियम-आधारित (AI बंद)',
    thinking: 'तर्क', step: 'चरण', no_farms: 'अभी कोई खेत पंजीकृत नहीं।', no_alerts: 'अभी कोई अलर्ट नहीं। स्कैन चलाएँ।',
    scan: 'स्कैन', delete: 'हटाएँ', scanning: 'स्कैन हो रहा है…', registered: 'खेत सेंटिनल निगरानी के लिए पंजीकृत हुआ।',
    new_alerts: 'नए अलर्ट', mic_unsupported: 'इस ब्राउज़र में आवाज़ से इनपुट उपलब्ध नहीं (Chrome आज़माएँ)।',
    no_voice: 'इस डिवाइस पर इस भाषा की आवाज़ इंस्टॉल नहीं है।',
    q1: 'इस हफ्ते मुझे खेत में क्या करना चाहिए?', q2: 'क्या अगले 3 दिनों में सिंचाई करनी चाहिए?',
    q3: 'इस हफ्ते छिड़काव का सबसे अच्छा समय कब है?', q4: 'आज मंडी भाव क्या है और कहाँ बेचूँ?',
    q5: 'क्या बाढ़, चक्रवात या गर्मी का कोई खतरा है?', q6: 'उपग्रह से मेरी फसल की सेहत कैसी है?',
    q7: 'मेरी खेत की ताज़ा फोटो में क्या दिख रहा है?',
    field_size: 'खेत का आकार एकड़ में (वैकल्पिक)', tab_diary: 'खेत डायरी', diary_title: 'साप्ताहिक जाँच',
    diary_body: 'हर हफ्ते फसल की एक फोटो और कुछ टैप। AI पिछले हफ्ते से तुलना करके हर सलाह को आपके खेत के अनुसार बनाता है।',
    photo_hint: '📷 फसल की फोटो लें या चुनें (1–2 मीटर दूर से, दिन की रोशनी में)', q_leaf: 'पत्तियों का रंग', q_pests: 'कीट दिखे',
    leaf_green: 'हरा', leaf_yellowing: 'पीलापन', leaf_brown: 'भूरे धब्बे', leaf_mixed: 'मिला-जुला',
    pests_none: 'नहीं', pests_few: 'थोड़े', pests_many: 'बहुत', q_irrigated: 'सिंचाई की तारीख', q_sprayed: 'छिड़काव की तारीख',
    q_fertilised: 'खाद की तारीख', q_note: 'और कुछ देखा?', diary_submit: 'जाँच सहेजें',
    diary_history: 'आपकी खेत डायरी', diary_saving: 'सहेजा जा रहा है और फोटो का विश्लेषण हो रहा है…', diary_saved: 'जाँच सहेजी गई।',
    diary_need_input: 'फोटो जोड़ें या कम से कम एक सवाल का जवाब दें।', diary_empty: 'अभी कोई जाँच नहीं। इस हफ्ते की फसल फोटो जोड़ें।',
    diary_add: 'इस हफ्ते की फोटो जोड़ें', diary_due: 'अगली जाँच', diary_overdue: 'इस हफ्ते की फोटो बाकी है',
    ai_unavailable: 'फोटो सहेजी गई। AI विश्लेषण के लिए AI key चाहिए।', ai_failed: 'फोटो सहेजी गई। इस बार AI इसका विश्लेषण नहीं कर सका।',
    stage_seen: 'दिखी अवस्था', stage_expected: 'अपेक्षित अवस्था', canopy: 'पत्तों का फैलाव', water_stress: 'पानी की कमी',
    weeds: 'खरपतवार', change: 'पिछली फोटो से बदलाव', next_checks: 'खेत में जाँचें', photo_quality: 'फोटो गुणवत्ता',
    delete_entry: 'हटाएँ', irrigation_updated: 'आपकी जाँच से पिछली सिंचाई की तारीख अपडेट हुई।',
  },
};

let uiLang = 'en';

export function setUiLanguage(code) {
  uiLang = STRINGS[code] ? code : 'en';
  document.documentElement.lang = code || 'en';
}

export function t(key) {
  return (STRINGS[uiLang] && STRINGS[uiLang][key]) || STRINGS.en[key] || key;
}

export function applyI18n(root = document) {
  root.querySelectorAll('[data-i18n]').forEach((el) => { el.textContent = t(el.dataset.i18n); });
  root.querySelectorAll('[data-i18n-placeholder]').forEach((el) => { el.placeholder = t(el.dataset.i18nPlaceholder); });
}
