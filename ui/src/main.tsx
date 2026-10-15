import { render } from "preact";

import { setLocale } from "./format";
import { languageChoice, resolveLanguage, setLanguage } from "./i18n";
import { initTheme } from "./store";
import "./styles/tokens.css";
import "./styles/app.css";
import "./styles/search.css";
import "./styles/pages.css";
import "./styles/editor.css";
import { Root } from "./views/access";

const lang = resolveLanguage(languageChoice(), navigator.languages ?? [navigator.language]);
setLanguage(lang);
setLocale(lang === "en" ? "en-GB" : "de-DE");
initTheme();
const root = document.getElementById("app");
if (root) render(<Root />, root);
