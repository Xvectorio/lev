import { mount } from 'svelte';
import App from './App.svelte';
import './style.css';
let theme = 'system';
try { theme = localStorage.getItem('lev-theme') || 'system'; } catch {}
if (!['light', 'dark', 'system'].includes(theme)) theme = 'system';
document.documentElement.dataset.themePreference = theme;
document.documentElement.dataset.theme = theme === 'system' ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : theme;
mount(App, { target: document.getElementById('app')! });
