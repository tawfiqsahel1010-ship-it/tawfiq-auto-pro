(() => {
  const $ = (s, root = document) => root.querySelector(s);
  const intro = $("#intro");
  const enter = $("#enterSite");
  const skip = $("#skipIntro");
  const languageButton = $("#languageButton");
  const languageMenu = $("#languageMenu");
  const currentLanguage = $("#currentLanguage");
  const video = $("#heroVideo");

  const translations = {
    fa: {
      label:"فارسی", dir:"rtl", introText:"قدرت در حرکت، دقت در مدیریت",enter:"ورود به تجربه لوکس",skip:"رد شدن ←",
      navHome:"خانه",navFeatures:"امکانات",navAbout:"درباره سیستم",login:"ورود",
      eyebrow:"فراتر از مدیریت معمولی",headline:"هر موتر،<br><em>یک حساب دقیق.</em>",
      description:"موترها، مصارف، عواید و گزارش‌های خود را در یک فضای خصوصی، منظم و حرفه‌ای مدیریت کنید.",
      register:"ساخت حساب کاربری",discover:"کشف امکانات",privacy:"حساب اختصاصی · مدیریت آسان · اطلاعات خصوصی",
      sideText:"تجربه‌ای متفاوت برای مدیریت هوشمند موترهای شما.",scroll:"برای کشف امکانات پایین بروید",
      featureHeading:"مدیریت دقیق؛<em> انتخاب هوشمندانه</em>",featureDescription:"ابزارهای ضروری برای کنترل بهتر موترها و حساب‌وکتاب شما.",
      f1title:"مدیریت موترها",f1desc:"مشخصات موترها را ثبت کنید و همه اطلاعات را منظم نگه دارید.",
      f2title:"ثبت مصارف",f2desc:"مصارف ترمیم، تیل و دیگر هزینه‌ها را به‌صورت دقیق ثبت کنید.",
      f3title:"عواید و حسابداری",f3desc:"عواید و مصارف را بررسی کنید و گزارش‌های روشن داشته باشید.",
      f4title:"حساب خصوصی",f4desc:"اطلاعات هر کاربر در فضای اختصاصی خودش مدیریت می‌شود.",
      closingHeading:"کنترل بیشتر.<br><em>تصمیم‌های بهتر.</em>",closingText:"یک راه ساده و حرفه‌ای برای مدیریت موترها و حساب‌وکتاب.",
      startNow:"به‌زودی همراه ما شوید",footerText:"قدرت در حرکت، دقت در مدیریت."
    },
    prs: {
      label:"دری",dir:"rtl",introText:"قدرت در حرکت، دقت در مدیریت",enter:"ورود به تجربه لوکس",skip:"رد شدن ←",
      navHome:"خانه",navFeatures:"امکانات",navAbout:"درباره سیستم",login:"ورود",
      eyebrow:"فراتر از مدیریت معمولی",headline:"هر موتر،<br><em>یک حساب دقیق.</em>",
      description:"موترها، مصارف، عواید و گزارش‌های خود را در یک فضای خصوصی، منظم و حرفه‌ای مدیریت کنین.",
      register:"ساخت حساب کاربری",discover:"دیدن امکانات",privacy:"حساب اختصاصی · مدیریت آسان · معلومات خصوصی",
      sideText:"تجربه‌ای متفاوت برای مدیریت هوشمند موترهای شما.",scroll:"برای دیدن امکانات پایین برین",
      featureHeading:"مدیریت دقیق؛<em> انتخاب هوشمندانه</em>",featureDescription:"ابزارهای ضروری برای کنترول بهتر موترها و حساب‌وکتاب شما.",
      f1title:"مدیریت موترها",f1desc:"مشخصات موترهای خود را ثبت کنین و معلومات را منظم نگه دارین.",
      f2title:"ثبت مصارف",f2desc:"مصارف ترمیم، تیل و دیگر هزینه‌ها را به‌صورت دقیق ثبت کنین.",
      f3title:"عواید و حسابداری",f3desc:"عواید و مصارف را بررسی کنین و گزارش‌های واضح داشته باشین.",
      f4title:"حساب خصوصی",f4desc:"معلومات هر کاربر در حساب اختصاصی خودش نگهداری می‌شود.",
      closingHeading:"کنترول بیشتر.<br><em>تصمیم‌های بهتر.</em>",closingText:"یک راه ساده و حرفه‌ای برای مدیریت موترها و حساب‌وکتاب.",
      startNow:"به‌زودی همراه ما شوید",footerText:"قدرت در حرکت، دقت در مدیریت."
    },
    en: {
      label:"English",dir:"ltr",introText:"POWER IN MOTION. PRECISION IN MANAGEMENT.",enter:"ENTER THE EXPERIENCE",skip:"SKIP INTRO →",
      navHome:"Home",navFeatures:"Features",navAbout:"About",login:"Sign in",
      eyebrow:"BEYOND ORDINARY MANAGEMENT",headline:"Every vehicle,<br><em>accounted for.</em>",
      description:"Manage your vehicles, expenses, income, and reports in one private, organized, professional space.",
      register:"Create your account",discover:"Explore features",privacy:"Private account · Easy management · Your data",
      sideText:"A refined experience for smarter vehicle management.",scroll:"SCROLL TO DISCOVER",
      featureHeading:"Precision in management.<br><em>Confidence in every decision.</em>",featureDescription:"Essential tools to manage your vehicles and finances with confidence.",
      f1title:"Vehicle Management",f1desc:"Register your vehicles and keep their information organized.",
      f2title:"Expense Tracking",f2desc:"Track maintenance, fuel, and other vehicle-related expenses.",
      f3title:"Income & Accounting",f3desc:"Review income and expenses with clear, useful records.",
      f4title:"Private Workspace",f4desc:"Each user manages their information in a separate account.",
      closingHeading:"More control.<br><em>Better decisions.</em>",closingText:"A smarter, simpler way to manage your vehicles and finances.",
      startNow:"Get started soon",footerText:"Power in motion. Precision in management."
    }
  };

  function setLanguage(lang) {
    const t = translations[lang] || translations.prs;
    document.documentElement.lang = lang === "prs" ? "fa-AF" : lang;
    document.documentElement.dir = t.dir;
    document.querySelectorAll("[data-i18n]").forEach(el => {
      const key = el.dataset.i18n;
      if (t[key] !== undefined) el.textContent = t[key];
    });
    document.querySelectorAll("[data-i18n-html]").forEach(el => {
      const key = el.dataset.i18nHtml;
      if (t[key] !== undefined) el.innerHTML = t[key];
    });
    currentLanguage.textContent = t.label;
    try { localStorage.setItem("tawfiq_language", lang); } catch (_) {}
    languageMenu.classList.remove("open");
    languageButton.setAttribute("aria-expanded", "false");
  }

  languageButton.addEventListener("click", () => {
    const open = languageMenu.classList.toggle("open");
    languageButton.setAttribute("aria-expanded", String(open));
  });

  languageMenu.querySelectorAll("[data-lang]").forEach(button => {
    button.addEventListener("click", () => setLanguage(button.dataset.lang));
  });

  document.addEventListener("click", event => {
    if (!event.target.closest(".language-wrap")) {
      languageMenu.classList.remove("open");
      languageButton.setAttribute("aria-expanded", "false");
    }
  });

  function closeIntro() {
    if (!intro) return;
    intro.classList.add("hidden");
    document.body.classList.remove("intro-active");
    try { sessionStorage.setItem("tawfiq_intro_seen", "1"); } catch (_) {}
  }

  enter.addEventListener("click", closeIntro);
  skip.addEventListener("click", closeIntro);

  try {
    if (sessionStorage.getItem("tawfiq_intro_seen") === "1") {
      intro.classList.add("hidden");
    } else {
      document.body.classList.add("intro-active");
    }
    const savedLanguage = localStorage.getItem("tawfiq_language");
    setLanguage(savedLanguage && translations[savedLanguage] ? savedLanguage : "prs");
  } catch (_) {
    setLanguage("prs");
  }

  if (video) {
    video.muted = true;
    video.play().catch(() => {});
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) video.play().catch(() => {});
    });
  }
})();
