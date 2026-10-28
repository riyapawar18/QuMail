import React, { useState, useEffect, useRef } from 'react';
import { 
  Shield, Mail, Lock, Plus, RefreshCcw, Send, 
  FileText, Trash2, ShieldCheck, User as UserIcon, Search,
  Bell, Calendar, Star, ChevronDown, Radio, Inbox, Key,
  Paperclip, X, Download, Image, File, Menu, Clock, SendHorizontal, ShoppingCart, AlertCircle, Trash, MoreVertical, Maximize2, Minimize2, Bold, Italic, Underline, Type, AlignLeft, List, ListOrdered, Quote, RotateCcw, RotateCw, Link as LinkIcon, Smile as SmileIcon, GripVertical,
  FolderLock, LogOut, Info
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { API_BASE, apiFetch, readJson } from './api';
import DocumentVault from './DocumentVault';

const DEMO_NOTICE = "Demo only — not sent to Gmail";


const safeSetLocal = (key, value) => {
  try {
    localStorage.setItem(key, value);
  } catch (e) {
    console.warn('Storage quota exceeded for', key);
  }
};

// Returns a localStorage key scoped to the active account email
// so switching accounts never mixes up mail data
const mailKey = (suffix, email) => {
  const safe = (email || 'default').replace(/[@.]/g, '_');
  return `qumail_${safe}_${suffix}`;
};

const formatMailDate = (iso) => {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  if (sameDay) {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  if (date.getFullYear() === now.getFullYear()) {
    return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
  }
  return date.toLocaleDateString([], { year: 'numeric', month: 'short', day: 'numeric' });
};

const makeRequestId = () => {
  try {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  } catch (_) {}
  return `req-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
};

const QuMailApp = ({ auth, onExitDemo, onSignOut }) => {
  const isDemo = auth?.mode === 'demo';
  // --- STATE MANAGEMENT ---
  const [isBackendOnline, setIsBackendOnline] = useState(true);
  const [activeTab, setActiveTab] = useState('inbox');
  const [isComposeOpen, setIsComposeOpen] = useState(false);
  const [isVaultOpen, setIsVaultOpen] = useState(false);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [isLessExpanded, setIsLessExpanded] = useState(false);
  const [isSecurityDropdownOpen, setIsSecurityDropdownOpen] = useState(false);
  const [isMoreMenuOpen, setIsMoreMenuOpen] = useState(false);
  const [hoveredSecurity, setHoveredSecurity] = useState(null);
  const [isProfileMenuOpen, setIsProfileMenuOpen] = useState(false);
  const [accounts, setAccounts] = useState([]);
  const [isAddingAccount, setIsAddingAccount] = useState(false);
  const profileMenuRef = useRef(null);
  const [emails, setEmails] = useState([]);
  const [sentEmails, setSentEmails] = useState([]);
  const [draftEmails, setDraftEmails] = useState([]);
  const [trashEmails, setTrashEmails] = useState([]);
  const [snoozedEmails, setSnoozedEmails] = useState([]);
  const [spamEmails, setSpamEmails] = useState([]);
  const [scheduledEmails, setScheduledEmails] = useState([]);
  const [purchasesEmails, setPurchasesEmails] = useState([]);

  const [userProfile, setUserProfile] = useState(auth?.profile || null);
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingOlder, setIsLoadingOlder] = useState(false);
  const [gmailError, setGmailError] = useState('');
  const [gmailNextTokens, setGmailNextTokens] = useState({ inbox: '', sent: '' });
  const [sentLoaded, setSentLoaded] = useState(false);
  const [decrypting, setDecrypting] = useState(false);
  const [decryptError, setDecryptError] = useState('');
  const [sendStatus, setSendStatus] = useState(null);
  const pollingRef = useRef(false);
  const sendInFlightRef = useRef(false);
  const sendRequestRef = useRef({ signature: '', id: '' });
  const [selectedEmail, setSelectedEmail] = useState(null);
  const [blockedSenders, setBlockedSenders] = useState([]);
  const [searchQuery, setSearchQuery] = useState('');
  
  // Compose Form State
  const [recipient, setRecipient] = useState('');
  const [subject, setSubject] = useState('');
  const [message, setMessage] = useState('');
  const [securityLevel, setSecurityLevel] = useState(isDemo ? 4 : 0);
  const [attachedFiles, setAttachedFiles] = useState([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const fileInputRef = useRef(null);

  // --- CLOSE PROFILE MENU ON OUTSIDE CLICK ---
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (profileMenuRef.current && !profileMenuRef.current.contains(e.target)) {
        setIsProfileMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // --- FETCH ACCOUNTS ---
  const fetchAccounts = async () => {
    if (isDemo) {
      setAccounts([{
        email: userProfile?.email || 'guest@qumail.demo',
        name: userProfile?.name || 'Guest Demo',
        picture: null,
        active: true,
      }]);
      return;
    }
    try {
      const res = await fetch(`${API_BASE}/accounts`);
      const data = await res.json();
      setAccounts(data.accounts || []);
    } catch (_) {}
  };

  // Open profile menu and refresh account list
  const handleOpenProfileMenu = () => {
    setIsProfileMenuOpen(prev => {
      if (!prev) fetchAccounts();
      return !prev;
    });
  };

  // --- SWITCH ACCOUNT ---
  const handleSwitchAccount = async (email) => {
    if (userProfile?.email === email) return;
    try {
      const res = await fetch(`${API_BASE}/switch_account`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email })
      });
      const data = await res.json();
      if (data.status === 'switched') {
        setIsProfileMenuOpen(false);
        setSelectedEmail(null);
        setSentLoaded(false);
        setGmailNextTokens({ inbox: '', sent: '' });
        // Load this account's mail — backend active account is now updated
        await loadMailFromDisk(email);
        const profileRes = await fetch(`${API_BASE}/me`);
        const profile = await profileRes.json();
        if (profile && !profile.error) setUserProfile(profile);
        setIsLoading(true);
        try {
          await loadGmailFolder('inbox');
        } catch (err) {
          setGmailError(err.message || 'Could not load Gmail for this account.');
        } finally {
          setIsLoading(false);
        }
      } else {
        alert(data.error || 'Could not switch account.');
      }
    } catch (e) {
      alert('Switch failed: ' + e.message);
    }
  };

  // --- REMOVE ACCOUNT (permanent delete from all files) ---
  const handleRemoveAccount = async (emailToRemove) => {
    if (isDemo) {
      if (onExitDemo) onExitDemo();
      return;
    }
    const target = emailToRemove || userProfile?.email;
    const isActive = target === userProfile?.email;
    const confirmed = window.confirm(
      `Remove ${target} from QuMail?\n\nThis will permanently delete its token and encryption keys from all files. This cannot be undone.`
    );
    if (!confirmed) return;

    setIsProfileMenuOpen(false);
    try {
      const res = await fetch(`${API_BASE}/remove_account`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: target })
      });
      const data = await res.json();
      if (data.status === 'removed') {
        await fetchAccounts();
        if (data.new_active) {
          // Switch UI to the new active account
          const profileRes = await fetch(`${API_BASE}/me`);
          const profile = await profileRes.json();
          if (profile && !profile.error) setUserProfile(profile);
        } else {
          // No accounts left — return to the welcome screen
          if (onSignOut) onSignOut();
        }
      } else {
        alert(data.error || 'Could not remove account.');
      }
    } catch (e) {
      alert('Remove failed: ' + e.message);
    }
  };

  // --- VERIFY FILES ---
  const handleVerifyFiles = async () => {
    try {
      const res = await fetch(`${API_BASE}/verify_files`);
      const data = await res.json();
      const lines = Object.entries(data.files).map(([name, info]) => {
        const status = info.exists ? `✅ ${info.size_bytes}B` : '❌ MISSING';
        const extra = info.accounts ? ` [${info.accounts.join(', ')}]` : '';
        return `${status}  ${name}${extra}`;
      });
      alert(`File Verification\n\n${lines.join('\n')}\n\ntoken.json format: ${data.files['token.json']?.format || 'unknown'}`);
    } catch (e) {
      alert('Verify failed: ' + e.message);
    }
  };

  // --- ADD ACCOUNT ---
  const handleAddAccount = async () => {
    if (isDemo) {
      alert('Guest demo is isolated from Google accounts. Exit the demo to sign in with Google.');
      return;
    }
    setIsAddingAccount(true);
    try {
      const res = await fetch(`${API_BASE}/add_account`, { method: 'POST' });
      const data = await readJson(res);
      if (!res.ok || !data.auth_url) {
        throw new Error(data.error || 'Google OAuth is not configured on this deployment.');
      }
      window.location.href = data.auth_url;
    } catch (e) {
      alert('Add account failed: ' + e.message);
      setIsAddingAccount(false);
    }
  };

  // --- LOGOUT (sign out = remove current account) ---
  const handleLogout = async (emailToRemove) => {
    await handleRemoveAccount(emailToRemove || userProfile?.email);
  };

  // --- 1. INITIALIZATION: FETCH USER PROFILE ---
  useEffect(() => {
    if (isDemo) {
      setIsBackendOnline(true);
      return;
    }
    const fetchProfile = async () => {
      try {
        const response = await fetch(`${API_BASE}/me`);
        if (!response.ok) throw new Error("Status " + response.status);
        const data = await response.json();
        if (data && !data.error) {
          setUserProfile(data);
          setIsBackendOnline(true);
        }
      } catch (err) {
        setIsBackendOnline(false);
      }
    };
    fetchProfile();
  }, [isDemo]);

  // --- PUSH CURRENT STATE TO DISK (migration / sync) ---
  const syncStateToDisk = async (emailHint) => {
    const em = emailHint || userProfile?.email;
    if (!em) return;

    // Try per-account keys first, then fall back to legacy global keys
    const load = (suffix, legacyKey) => {
      try {
        // New per-account key
        const newKey = mailKey(suffix, em);
        const newVal = localStorage.getItem(newKey);
        if (newVal) return JSON.parse(newVal);
        // Legacy global key (old format before per-account storage)
        if (legacyKey) {
          const oldVal = localStorage.getItem(legacyKey);
          if (oldVal) return JSON.parse(oldVal);
        }
        return [];
      } catch { return []; }
    };

    const folders = {
      inbox:     load('inbox',     'qumail_inbox_items'),
      sent:      load('sent',      'qumail_sent_items'),
      drafts:    load('drafts',    'qumail_draft_items'),
      trash:     load('trash',     'qumail_trash_items'),
      snoozed:   load('snoozed',   'qumail_snoozed_items'),
      spam:      load('spam',      'qumail_spam_items'),
      scheduled: load('scheduled', 'qumail_scheduled_items'),
      purchases: load('purchases', 'qumail_purchases_items'),
    };
    const hasAny = Object.values(folders).some(arr => arr.length > 0);
    if (!hasAny) return;
    try {
      await fetch(`${API_BASE}/mail_store/sync_all`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: em, folders })
      });
    } catch (_) {}
  };

  // --- 1b. LOAD MAIL DATA FROM DISK STORE ---
  const loadMailFromDisk = async (emailHint) => {
    const em = emailHint || userProfile?.email;

    const loadLocal = (suffix, legacyKey) => {
      try {
        const v = localStorage.getItem(mailKey(suffix, em));
        if (v) return JSON.parse(v);
        if (legacyKey) {
          const v2 = localStorage.getItem(legacyKey);
          if (v2) return JSON.parse(v2);
        }
        return [];
      } catch { return []; }
    };

    try {
      // No email query param — backend reads active account from token.json
      // (switch_account already updated it before this is called)
      const res = await fetch(`${API_BASE}/mail_store`);
      const data = await res.json();

      if (data.status === 'ok' && data.folders) {
        const f = data.folders;
        const hasData = Object.values(f).some(arr => arr.length > 0);

        if (hasData) {
          setEmails(f.inbox || []);
          setSentEmails(f.sent || []);
          setDraftEmails(f.drafts || []);
          setTrashEmails(f.trash || []);
          setSnoozedEmails(f.snoozed || []);
          setSpamEmails(f.spam || []);
          setScheduledEmails(f.scheduled || []);
          setPurchasesEmails(f.purchases || []);
        } else {
          // Disk empty — migrate from localStorage then reload
          await syncStateToDisk(em);
          const res2 = await fetch(`${API_BASE}/mail_store`);
          const data2 = await res2.json();
          if (data2.status === 'ok' && data2.folders) {
            const f2 = data2.folders;
            setEmails(f2.inbox || []);
            setSentEmails(f2.sent || []);
            setDraftEmails(f2.drafts || []);
            setTrashEmails(f2.trash || []);
            setSnoozedEmails(f2.snoozed || []);
            setSpamEmails(f2.spam || []);
            setScheduledEmails(f2.scheduled || []);
            setPurchasesEmails(f2.purchases || []);
          }
        }
      }
    } catch (e) {
      console.warn('Disk store unavailable, loading from localStorage:', e);
      setEmails(loadLocal('inbox', 'qumail_inbox_items'));
      setSentEmails(loadLocal('sent', 'qumail_sent_items'));
      setDraftEmails(loadLocal('drafts', 'qumail_draft_items'));
      setTrashEmails(loadLocal('trash', 'qumail_trash_items'));
      setSnoozedEmails(loadLocal('snoozed', 'qumail_snoozed_items'));
      setSpamEmails(loadLocal('spam', 'qumail_spam_items'));
      setScheduledEmails(loadLocal('scheduled', 'qumail_scheduled_items'));
      setPurchasesEmails(loadLocal('purchases', 'qumail_purchases_items'));
    }
    setSelectedEmail(null);
  };

  // --- DEMO MAILBOX (in-memory guest session, no Gmail) ---
  const loadDemoMail = async () => {
    try {
      const [inboxRes, sentRes] = await Promise.all([
        apiFetch('/api/demo/inbox'),
        apiFetch('/api/demo/sent'),
      ]);
      const inboxData = await readJson(inboxRes);
      const sentData = await readJson(sentRes);
      if (!inboxRes.ok || !sentRes.ok) {
        throw new Error(inboxData.error || sentData.error || 'Demo session expired.');
      }
      setEmails(inboxData.messages || []);
      setSentEmails(sentData.messages || []);
      setIsBackendOnline(true);
    } catch (err) {
      setIsBackendOnline(false);
      console.warn('Demo mailbox unavailable:', err.message);
    } finally {
      setSelectedEmail(null);
    }
  };

  // --- REAL GMAIL MAILBOX (server-side Gmail API, tokens never reach the browser) ---
  const mapGmailMessage = (m) => ({
    id: m.id,
    sender: m.sender || m.sender_email || 'Unknown',
    senderName: m.sender_name || m.sender_email || 'Unknown',
    senderEmail: m.sender_email || '',
    recipient: m.recipient || m.recipient_email || '',
    subject: m.subject || '(No subject)',
    preview: m.snippet || '',
    time: formatMailDate(m.date),
    date: m.date,
    read: !m.unread,
    starred: false,
    qumail: !!m.qumail,
    level: null,
    attachments: [],
  });

  const loadGmailFolder = async (folder, { pageToken = '', append = false, silent = false } = {}) => {
    const params = new URLSearchParams({ folder, max_results: '20' });
    if (pageToken) params.set('page_token', pageToken);
    const res = await fetch(`${API_BASE}/api/gmail/messages?${params.toString()}`);
    const data = await readJson(res);
    if (!res.ok) {
      throw new Error(data.error || `Could not load the Gmail ${folder}.`);
    }
    const mapped = (data.messages || []).map(mapGmailMessage);
    if (folder === 'sent') {
      setSentEmails(prev => (append ? [...prev, ...mapped.filter(m => !prev.some(p => p.id === m.id))] : mapped));
      setSentLoaded(true);
    } else {
      setEmails(prev => (append ? [...prev, ...mapped.filter(m => !prev.some(p => p.id === m.id))] : mapped));
    }
    setGmailNextTokens(prev => ({ ...prev, [folder]: data.next_page_token || '' }));
    setIsBackendOnline(true);
    if (!silent) setGmailError('');
    return data;
  };

  useEffect(() => {
    if (!userProfile?.email) return;
    if (isDemo) {
      loadDemoMail();
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setGmailError('');
    setSentLoaded(false);
    setGmailNextTokens({ inbox: '', sent: '' });
    loadMailFromDisk(userProfile.email)
      .then(() => loadGmailFolder('inbox'))
      .catch(err => {
        if (!cancelled) {
          setGmailError(err.message || 'Gmail inbox is unavailable.');
          setIsBackendOnline(false);
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => { cancelled = true; };
  }, [userProfile?.email, isDemo]);

  // Load Sent lazily the first time that tab is opened.
  useEffect(() => {
    if (isDemo || !userProfile?.email) return;
    if (activeTab !== 'sent' || sentLoaded) return;
    let cancelled = false;
    setIsLoading(true);
    loadGmailFolder('sent')
      .catch(err => {
        if (!cancelled) setGmailError(err.message || 'Gmail Sent is unavailable.');
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => { cancelled = true; };
  }, [activeTab, sentLoaded, isDemo, userProfile?.email]);

  // Poll the visible inbox while the page is open. Requests never overlap.
  useEffect(() => {
    if (isDemo || !userProfile?.email) return;
    const intervalMs = parseInt(process.env.REACT_APP_GMAIL_POLL_MS || '60000', 10);
    if (!intervalMs || intervalMs < 15000) return;

    let cancelled = false;
    const tick = async () => {
      if (cancelled || document.visibilityState !== 'visible' || pollingRef.current) return;
      pollingRef.current = true;
      try {
        await loadGmailFolder('inbox', { silent: true });
      } catch (_) {
        /* transient polling failure; keep the last good list */
      } finally {
        pollingRef.current = false;
      }
    };
    const timer = setInterval(tick, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [isDemo, userProfile?.email]);

  const loadOlderMessages = async () => {
    const folder = activeTab === 'sent' ? 'sent' : 'inbox';
    const token = gmailNextTokens[folder];
    if (!token || isLoadingOlder) return;
    setIsLoadingOlder(true);
    try {
      await loadGmailFolder(folder, { pageToken: token, append: true });
    } catch (err) {
      setGmailError(err.message || 'Could not load older messages.');
    } finally {
      setIsLoadingOlder(false);
    }
  };

  // --- 2. SYNC & REFRESH LOGIC ---
  const handleRefresh = async () => {
    if (isDemo) {
      setIsLoading(true);
      try {
        await loadDemoMail();
      } finally {
        setIsLoading(false);
      }
      return;
    }
    setIsLoading(true);
    setGmailError('');
    try {
      await loadGmailFolder('inbox');
      if (sentLoaded || activeTab === 'sent') {
        await loadGmailFolder('sent');
      }
    } catch (err) {
      setGmailError(err.message || 'Could not refresh Gmail.');
    } finally {
      setIsLoading(false);
    }
  };
// --- TOGGLE STAR ---
  const toggleStar = (e, emailId, isInbox) => {
    e.stopPropagation();
    if (isInbox) {
      setEmails(prev => {
        const updated = prev.map(m => m.id === emailId ? { ...m, starred: !m.starred } : m);
        safeSetLocal(mailKey('inbox', userProfile?.email), JSON.stringify(updated));
        return updated;
      });
      if (selectedEmail?.id === emailId) {
        setSelectedEmail(prev => ({ ...prev, starred: !prev.starred }));
      }
    } else {
      setSentEmails(prev => {
        const updated = prev.map(m => m.id === emailId ? { ...m, starred: !m.starred } : m);
        safeSetLocal(mailKey('sent', userProfile?.email), JSON.stringify(updated));
        return updated;
      });
      if (selectedEmail?.id === emailId) {
        setSelectedEmail(prev => ({ ...prev, starred: !prev.starred }));
      }
    }
  };

  // --- MOVE TO TRASH ---
  const moveToTrash = async (emailId) => {
    if (isDemo) {
      const emailObj =
        emails.find(e => e.id === emailId) ||
        sentEmails.find(e => e.id === emailId) ||
        draftEmails.find(e => e.id === emailId);
      if (emailObj) {
        setTrashEmails(prev => [emailObj, ...prev]);
        setEmails(prev => prev.filter(e => e.id !== emailId));
        setSentEmails(prev => prev.filter(e => e.id !== emailId));
        setDraftEmails(prev => prev.filter(e => e.id !== emailId));
      }
      if (activeTab === 'trash') setTrashEmails(prev => prev.filter(e => e.id !== emailId));
      setSelectedEmail(null);
      return;
    }
    // Determine source folder based on active tab or search
    if (activeTab === 'starred' || activeTab === 'important' || activeTab === 'all') {
      // Find where it actually is, otherwise there is nothing to remove
      if (!emails.find(e => e.id === emailId) &&
          !sentEmails.find(e => e.id === emailId) &&
          !draftEmails.find(e => e.id === emailId) &&
          !snoozedEmails.find(e => e.id === emailId) &&
          !scheduledEmails.find(e => e.id === emailId)) return;
    }

    if (activeTab === 'trash') {
      // If already in trash, delete permanently from the local disk store
      fetch(`${API_BASE}/mail_store/trash/${emailId}`, { method: 'DELETE' }).catch(() => {});
      setTrashEmails(prev => prev.filter(e => e.id !== emailId));
      setSelectedEmail(null);
      return;
    }

    // Real Gmail messages live in Gmail, not the local disk store: trash them via the API.
    const emailObj =
      emails.find(e => e.id === emailId) ||
      sentEmails.find(e => e.id === emailId) ||
      draftEmails.find(e => e.id === emailId) ||
      snoozedEmails.find(e => e.id === emailId) ||
      scheduledEmails.find(e => e.id === emailId);

    try {
      const res = await fetch(`${API_BASE}/api/gmail/messages/${encodeURIComponent(emailId)}/trash`, {
        method: 'POST',
      });
      const data = await readJson(res);
      if (!res.ok) throw new Error(data.error || 'Could not move this message to Trash.');

      setEmails(prev => prev.filter(e => e.id !== emailId));
      setSentEmails(prev => prev.filter(e => e.id !== emailId));
      setDraftEmails(prev => prev.filter(e => e.id !== emailId));
      setSnoozedEmails(prev => prev.filter(e => e.id !== emailId));
      setScheduledEmails(prev => prev.filter(e => e.id !== emailId));
      if (emailObj) setTrashEmails(prev => [emailObj, ...prev]);
      setGmailError('');
    } catch (err) {
      setGmailError(err.message || 'Could not move this message to Trash.');
    }

    setSelectedEmail(null);
  };

  const emptyTrash = () => {
    if (window.confirm("Are you sure you want to permanently delete all messages in the bin?")) {
      trashEmails.forEach(mail => {
        fetch(`${API_BASE}/mail_store/trash/${mail.id}`, { method: 'DELETE' }).catch(() => {});
      });
      setTrashEmails([]);
      setSelectedEmail(null);
    }
  };


  // --- TOGGLE IMPORTANT ---
  const toggleImportant = (e, emailId) => {
    if (e) e.stopPropagation();
    const update = (list) => list.map(m => m.id === emailId ? { ...m, important: !m.important } : m);
    setEmails(prev => {
      const updated = update(prev);
      safeSetLocal(mailKey('inbox', userProfile?.email), JSON.stringify(updated));
      return updated;
    });
    setSentEmails(prev => {
      const updated = update(prev);
      safeSetLocal(mailKey('sent', userProfile?.email), JSON.stringify(updated));
      return updated;
    });
    if (selectedEmail?.id === emailId) {
      setSelectedEmail(prev => ({ ...prev, important: !prev.important }));
    }
  };



  // --- TOGGLE READ ---
  const toggleRead = (e, emailId, forceState = null) => {
    if (e) e.stopPropagation();
    const update = (list) => list.map(m => m.id === emailId ? { ...m, read: forceState !== null ? forceState : !m.read } : m);
    const current = emails.find(m => m.id === emailId) || sentEmails.find(m => m.id === emailId);
    if (!isDemo && current) {
      const unread = forceState !== null ? !forceState : !!current.read;
      fetch(`${API_BASE}/api/gmail/messages/${encodeURIComponent(emailId)}/read`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ unread }),
      })
        .then(async (res) => {
          if (!res.ok) {
            const data = await readJson(res).catch(() => ({}));
            throw new Error(data.error || 'Gmail did not accept the read-state change.');
          }
        })
        .catch((err) => setGmailError(err.message || 'Could not update the read state in Gmail.'));
    }
    setEmails(prev => {
      const updated = update(prev);
      safeSetLocal(mailKey('inbox', userProfile?.email), JSON.stringify(updated));
      return updated;
    });
    setSentEmails(prev => {
      const updated = update(prev);
      safeSetLocal(mailKey('sent', userProfile?.email), JSON.stringify(updated));
      return updated;
    });
    if (selectedEmail?.id === emailId) {
      setSelectedEmail(prev => ({ ...prev, read: forceState !== null ? forceState : !prev.read }));
    }
  };

  // --- MOVE TO SNOOZED ---
  const moveToSnoozed = (emailId) => {
    let emailToMove = emails.find(e => e.id === emailId) || sentEmails.find(e => e.id === emailId);
    if (!emailToMove) return;

    setEmails(prev => {
        const updated = prev.filter(e => e.id !== emailId);
        safeSetLocal(mailKey('inbox', userProfile?.email), JSON.stringify(updated));
        return updated;
    });
    setSnoozedEmails(prev => {
        const updated = [{...emailToMove, snoozedAt: Date.now()}, ...prev];
        safeSetLocal(mailKey('snoozed', userProfile?.email), JSON.stringify(updated));
        return updated;
    });
    setSelectedEmail(null);
    alert("Message snoozed until tomorrow");
  };

  // --- MOVE TO SCHEDULED ---
  const moveToScheduled = (emailId) => {
    let emailToMove = emails.find(e => e.id === emailId) || sentEmails.find(e => e.id === emailId);
    if (!emailToMove) return;

    setEmails(prev => prev.filter(e => e.id !== emailId));
    setScheduledEmails(prev => {
        const updated = [{...emailToMove, scheduledTime: 'Tomorrow at 8:00 AM'}, ...prev];
        safeSetLocal(mailKey('scheduled', userProfile?.email), JSON.stringify(updated));
        return updated;
    });
    setSelectedEmail(null);
    alert("Message scheduled");
  };

  // --- OPEN MESSAGE (demo/Gmail detail; full content is fetched on demand) ---
  const handleSelectEmail = async (email) => {
    setSelectedEmail({ ...email, detail_loading: true, detail_error: '' });
    setDecryptError('');
    setDecrypting(false);
    const markRead = (list) => list.map(e => (e.id === email.id ? { ...e, read: true } : e));
    if (isDemo) {
      setEmails(markRead);
      setSentEmails(markRead);
      try {
        const res = await apiFetch(`/api/demo/messages/${encodeURIComponent(email.id)}`);
        if (res.ok) {
          const data = await readJson(res);
          if (data.message) {
            setSelectedEmail(prev => (prev && prev.id === email.id ? { ...prev, ...data.message } : prev));
          }
        }
      } catch (_) {}
      setSelectedEmail(prev => (prev && prev.id === email.id ? { ...prev, detail_loading: false } : prev));
      return;
    }

    setEmails(prev => (prev.some(e => e.id === email.id) ? markRead(prev) : prev));
    setSentEmails(prev => (prev.some(e => e.id === email.id) ? markRead(prev) : prev));

    try {
      const res = await fetch(`${API_BASE}/api/gmail/messages/${encodeURIComponent(email.id)}`);
      const data = await readJson(res);
      if (!res.ok) throw new Error(data.error || 'Could not open this Gmail message.');
      const m = data.message || {};
      setSelectedEmail(prev => (prev && prev.id === email.id ? {
        ...prev,
        sender: m.sender || prev.sender,
        senderName: m.sender_name || prev.senderName,
        senderEmail: m.sender_email || '',
        recipient: m.recipient || prev.recipient,
        subject: m.subject || prev.subject,
        body_html: m.body_html || '',
        body_text: m.body_text || '',
        decrypted_text: m.body_text || '',
        preview: m.body_text ? m.body_text.slice(0, 200) : prev.preview,
        attachments: m.attachments || [],
        qumail: !!m.qumail,
        unread: false,
        read: true,
        html_sanitized: !!m.html_sanitized,
        detail_loading: false,
      } : prev));
    } catch (err) {
      setSelectedEmail(prev => (prev && prev.id === email.id ? {
        ...prev,
        detail_loading: false,
        detail_error: err.message || 'Could not open this Gmail message.',
      } : prev));
    }
  };

  const handleDecryptSelected = async () => {
    const target = selectedEmail;
    if (!target || decrypting) return;
    setDecrypting(true);
    setDecryptError('');
    try {
      const res = await fetch(`${API_BASE}/api/gmail/messages/${encodeURIComponent(target.id)}/decrypt`, {
        method: 'POST',
      });
      const data = await readJson(res);
      if (!res.ok) throw new Error(data.error || 'Decryption failed.');
      const decryptedAttachments = (data.attachments || []).map(att => ({
        name: att.name,
        size: att.size,
        file_data_b64: att.data_b64 || null,
        encryption: null,
      }));
      setSelectedEmail(prev => (prev && prev.id === target.id ? {
        ...prev,
        qumail_decrypted: true,
        decrypted_text: data.text || (decryptedAttachments.length ? `[${decryptedAttachments.length} decrypted file(s)]` : ''),
        decrypted_attachments: decryptedAttachments,
      } : prev));
    } catch (err) {
      setDecryptError(err.message || 'Decryption failed.');
    } finally {
      setDecrypting(false);
    }
  };

  // --- SAVE DRAFT ---
  const handleSaveDraft = () => {
    if (!recipient && !message) {
      setIsComposeOpen(false);
      return;
    }
    const newDraft = {
      id: Date.now(),
      sender: userProfile?.name || "Me",
      recipient: recipient || "(No Recipient)",
      subject: subject || "(No Subject)",
      preview: message || "(Empty Message)",
      time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      level: securityLevel,
      starred: false
    };
    setDraftEmails(prev => {
      const updated = [newDraft, ...prev];
      safeSetLocal(mailKey('drafts', userProfile?.email), JSON.stringify(updated));
      return updated;
    });
    setIsComposeOpen(false);
    setMessage('');
    setRecipient('');
    setSubject('');
    alert("Saved as Draft");
  };

  // --- 3. SEND SECURE MESSAGE ---
  const handleSendMessage = async (e, forcedLevel = null) => {
    if (e && e.preventDefault) e.preventDefault();
    const activeLevel = forcedLevel !== null ? forcedLevel : securityLevel;

    if (!recipient) return alert("Please enter a recipient.");
    if (!message && attachedFiles.length === 0) return alert("Please enter a message or attach a file.");

    // CHECK: Level 1 (OTP) is strictly text-only
    if (activeLevel === 1 && attachedFiles.length > 0) {
      alert("⚠️ Level 1 (OTP) is strictly text-only. Please remove your attachments or choose a different security level (L2, L3, or L4) to send files.");
      setIsSecurityDropdownOpen(false);
      return;
    }

    // GUEST DEMO: encrypt with the per-session key; never touch Gmail.
    if (isDemo) {
      setIsLoading(true);
      setIsProcessing(true);
      try {
        const form = new FormData();
        form.append('recipient', recipient);
        form.append('subject', subject || '');
        form.append('body', message || '');
        attachedFiles.forEach(f => form.append('files', f.file, f.name));

        const res = await apiFetch('/api/demo/send', { method: 'POST', body: form });
        const data = await readJson(res);
        if (!res.ok || data.status !== 'demo-sent') {
          throw new Error(data.error || 'Demo send failed');
        }

        alert(`Demo only — encrypted with a per-session AES-256-GCM key.\nThis message was NOT sent to Gmail.`);
        await loadDemoMail();
        setActiveTab('sent');
        setIsComposeOpen(false);
        setMessage('');
        setRecipient('');
        setSubject('');
        setAttachedFiles([]);
      } catch (err) {
        alert('Demo send failed: ' + err.message);
      } finally {
        setIsLoading(false);
        setIsProcessing(false);
      }
      return;
    }

    if (sendInFlightRef.current) return;
    sendInFlightRef.current = true;
    setIsLoading(true);
    setIsProcessing(true);
    setSendStatus(null);

    try {
      const signature = [
        recipient,
        subject,
        message,
        activeLevel,
        attachedFiles.map(f => `${f.name}:${f.size}:${f.file?.lastModified || 0}`).join(','),
      ].join('|');
      if (sendRequestRef.current.signature !== signature) {
        sendRequestRef.current = { signature, id: makeRequestId() };
      }

      const form = new FormData();
      form.append('recipient', recipient);
      form.append('subject', subject || '');
      form.append('body', message || '');
      form.append('client_request_id', sendRequestRef.current.id);
      if (activeLevel === 0) {
        form.append('mode', 'plain');
      } else {
        form.append('mode', 'encrypted');
        form.append('level', String(activeLevel));
      }
      attachedFiles.forEach(f => form.append('files', f.file, f.name));

      const sendRes = await fetch(`${API_BASE}/api/gmail/send`, {
        method: 'POST',
        body: form,
      });
      const result = await readJson(sendRes);
      if (!sendRes.ok || result.status !== 'accepted') {
        throw new Error(result.error || 'Gmail did not accept the message.');
      }

      setSendStatus({
        type: 'success',
        text: `Gmail accepted message ${result.id} (${result.mode === 'encrypted' ? `L${result.level} encrypted` : 'standard, unencrypted'}). ${result.delivery_note || 'Delivery to the recipient is not guaranteed yet.'}`,
      });
      sendRequestRef.current = { signature: '', id: '' };
      setMessage('');
      setRecipient('');
      setSubject('');
      setAttachedFiles([]);
      setSecurityLevel(0);
      try {
        await loadGmailFolder('sent');
        setSentLoaded(true);
        setActiveTab('sent');
        setSelectedEmail(null);
      } catch (_) {}
    } catch (err) {
      // The request id is kept so an ambiguous failure can be retried without
      // Gmail accepting the same message twice.
      setSendStatus({ type: 'error', text: err.message || 'Send failed. Nothing was sent.' });
    } finally {
      sendInFlightRef.current = false;
      setIsLoading(false);
      setIsProcessing(false);
    }
  };

  const handleFileChange = async (e) => {
    if (e.target.files) {
      const selected = Array.from(e.target.files);
      
      const newFiles = selected.map(file => {
        const id = Math.random().toString(36).substr(2, 9);
        const isImage = file.type.startsWith('image/');
        const previewUrl = isImage ? URL.createObjectURL(file) : null;
        
        return {
          id,
          file,
          name: file.name,
          size: file.size,
          type: file.type,
          status: 'uploading', // Initial state
          previewUrl,
          payload: null
        };
      });

      setAttachedFiles(prev => [...prev, ...newFiles]);
      e.target.value = '';

      // Simulate a brief upload sequence for "uploaded" feel
      for (const f of newFiles) {
        await new Promise(r => setTimeout(r, 400));
        setAttachedFiles(prev => prev.map(item => 
          item.id === f.id ? { ...item, status: 'attached' } : item
        ));
      }
    }
  };

  // --- ATTACH A VAULT DOCUMENT TO THE CURRENT DRAFT ---
  const handleAttachFromVault = (file, doc) => {
    const id = Math.random().toString(36).slice(2, 11);
    setAttachedFiles(prev => [
      ...prev,
      {
        id,
        file,
        name: file.name,
        size: file.size,
        type: file.type || 'application/pdf',
        status: 'attached',
        previewUrl: null,
        payload: null,
        source: 'vault',
        docName: doc?.name,
      },
    ]);
    setIsVaultOpen(false);
    openCompose();
  };

  const openCompose = ({ recipient: nextRecipient, message: nextMessage } = {}) => {
    setSendStatus(null);
    if (nextRecipient !== undefined) setRecipient(nextRecipient);
    if (nextMessage !== undefined) setMessage(nextMessage);
    setIsComposeOpen(true);
  };

  // --- SIGN OUT / EXIT DEMO ---
  const handleSignOutClick = async () => {
    if (isDemo) {
      if (onExitDemo) onExitDemo();
      return;
    }
    try {
      await fetch(`${API_BASE}/logout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: userProfile?.email }),
      });
    } catch (_) {}
    if (onSignOut) onSignOut();
  };

  // We removed the auto-encryption useEffect to follow the "Encypt while sending" model.

  const detailEncryption = selectedEmail
    ? selectedEmail.encryption || selectedEmail.body_encryption || null
    : null;

  const detailAttachments = selectedEmail
    ? [
        ...(selectedEmail.attachments || []).filter(att => !att.encrypted),
        ...(selectedEmail.decrypted_attachments || []),
      ]
    : [];



  return (
    <div className="flex flex-col h-screen bg-background text-gray-800 font-sans overflow-hidden">
      {/* Hidden File Input for Attachments */}
      <input 
        type="file" 
        ref={fileInputRef} 
        onChange={handleFileChange} 
        multiple 
        className="opacity-0 w-0 h-0 absolute pointer-events-none" 
      />

      {/* Top Navbar */}
      <nav className="fixed top-0 left-0 right-0 z-50 px-6 py-3">
        <div className="glass-navbar pill-capsule h-16 flex items-center px-6 shadow-lg max-w-[1600px] mx-auto">
          <div className="flex items-center gap-3 w-[240px]">
            <img src="/logo.png" alt="QuMail Logo" className="w-11 h-11 object-contain drop-shadow-sm mix-blend-multiply" />
            <h1 className="text-[22px] font-bold tracking-tight text-gray-900 drop-shadow-sm">QuMail</h1>
            {isDemo && (
              <span className="hidden sm:inline-flex items-center gap-1 text-[9px] font-black uppercase tracking-[0.12em] px-2 py-1 rounded-full bg-amber-100 text-amber-700 border border-amber-200">
                <UserIcon size={10} /> Guest Demo
              </span>
            )}
          </div>

          <div className="flex-1 flex justify-center px-8">
            <div className="bg-white/40 hover:bg-white/50 transition-all flex items-center rounded-2xl h-11 px-4 w-full max-w-2xl border border-white/20 group focus-within:bg-white focus-within:shadow-inner focus-within:border-white/40">
              <Search className="w-5 h-5 text-gray-900 group-focus-within:text-black mr-3" />
              <input 
                type="text" 
                placeholder="Search mail" 
                className="bg-transparent border-none outline-none text-black focus:text-black placeholder-black/60 focus:placeholder-black/80 w-full text-[15px] font-medium" 
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
            </div>
          </div>

          <div className="flex items-center gap-2 w-[240px] justify-end">
            <button 
              onClick={handleRefresh} 
              title="Sync Secure" 
              className="p-2.5 hover:bg-white/20 rounded-full transition-colors relative group"
            >
              <RefreshCcw className={`w-[18px] h-[18px] text-white ${isLoading ? 'animate-spin' : ''}`}/>
              {isLoading && <span className="absolute -top-1 -right-1 flex h-3 w-3"><span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span><span className="relative inline-flex rounded-full h-3 w-3 bg-blue-500"></span></span>}
            </button>
            <button className="p-2.5 hover:bg-white/20 rounded-full transition-colors text-white"><Radio className="w-[18px] h-[18px]" /></button>
            <button className="p-2.5 hover:bg-white/20 rounded-full transition-colors text-white"><Bell className="w-[18px] h-[18px]" /></button>
            <button className="p-2.5 hover:bg-white/20 rounded-full transition-colors text-white mr-2"><Calendar className="w-[18px] h-[18px]" /></button>
            
            <div className="relative" ref={profileMenuRef}>
              <button
                onClick={handleOpenProfileMenu}
                className="relative cursor-pointer focus:outline-none"
                title={userProfile?.email || "Account"}
              >
                <div className="w-10 h-10 rounded-full bg-white text-gray-900 flex items-center justify-center font-bold text-sm shadow-md ring-2 ring-white/20 hover:ring-white transition-all overflow-hidden border-2 border-gray-100/10">
                  {userProfile?.name?.charAt(0).toUpperCase() || <UserIcon size={20} />}
                </div>
                <div className={`absolute bottom-0 right-0 w-3.5 h-3.5 rounded-full border-2 border-nav ${isBackendOnline ? 'bg-green-500' : 'bg-red-500 grayscale'}`}></div>
              </button>

              {/* Profile Dropdown */}
              <AnimatePresence>
                {isProfileMenuOpen && (
                  <motion.div
                    initial={{ opacity: 0, y: -8, scale: 0.95 }}
                    animate={{ opacity: 1, y: 0, scale: 1 }}
                    exit={{ opacity: 0, y: -8, scale: 0.95 }}
                    transition={{ duration: 0.15 }}
                    className="absolute right-0 top-14 w-80 bg-white rounded-2xl shadow-2xl border border-gray-100 z-[200] overflow-hidden"
                  >
                    {/* Header */}
                    <div className="px-4 py-3 bg-gray-50 border-b border-gray-100 flex items-center justify-between">
                      <span className="text-[13px] font-bold text-gray-700">{isDemo ? 'Guest session' : 'Accounts'}</span>
                      <span className="text-[11px] text-gray-400">{isDemo ? 'Demo' : `${accounts.length} signed in`}</span>
                    </div>

                    {/* Account List */}
                    <div className="max-h-64 overflow-y-auto">
                      {isDemo ? (
                        <div className="px-4 py-4">
                          <div className="flex items-center gap-3">
                            <div className="w-10 h-10 rounded-full bg-amber-100 text-amber-700 flex items-center justify-center font-bold text-sm shrink-0">
                              {userProfile?.name?.charAt(0).toUpperCase() || 'G'}
                            </div>
                            <div className="min-w-0 flex-1">
                              <p className="text-[13px] font-semibold text-gray-900 truncate">{userProfile?.name || 'Guest Demo'}</p>
                              <p className="text-[11px] text-gray-500 truncate">{userProfile?.email || 'guest@qumail.demo'}</p>
                            </div>
                            <span className="text-[9px] font-black uppercase tracking-[0.12em] px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 border border-amber-200 shrink-0">
                              Guest
                            </span>
                          </div>
                          <p className="text-[11px] text-gray-500 mt-3 leading-relaxed">
                            {userProfile?.notice || DEMO_NOTICE}. Mail and vault documents are synthetic and isolated to this browser session.
                          </p>
                        </div>
                      ) : (
                      <>
                      {accounts.length === 0 && (
                        <div className="px-5 py-4 text-[13px] text-gray-400 text-center">Loading accounts...</div>
                      )}
                      {accounts.map(acc => (
                        <div
                          key={acc.email}
                          className={`flex items-center gap-3 px-4 py-3 cursor-pointer transition-colors group ${acc.active ? 'bg-blue-50' : 'hover:bg-gray-50'}`}
                          onClick={() => handleSwitchAccount(acc.email)}
                        >
                          {/* Avatar */}
                          <div className={`w-10 h-10 rounded-full flex items-center justify-center font-bold text-sm shrink-0 ${acc.active ? 'bg-blue-600 text-white' : 'bg-gray-200 text-gray-700'}`}>
                            {acc.name?.charAt(0).toUpperCase() || <UserIcon size={18} />}
                          </div>
                          {/* Info */}
                          <div className="flex-1 min-w-0">
                            <p className="text-[13px] font-semibold text-gray-900 truncate">{acc.name || acc.email}</p>
                            <p className="text-[11px] text-gray-500 truncate">{acc.email}</p>
                          </div>
                          {/* Active badge */}
                          {acc.active && (
                            <div className="w-2 h-2 rounded-full bg-blue-500 shrink-0"></div>
                          )}
                          {/* Remove button — always visible on hover for all accounts */}
                          <button
                            onClick={e => { e.stopPropagation(); handleRemoveAccount(acc.email); }}
                            title="Remove account"
                            className="opacity-0 group-hover:opacity-100 p-1.5 rounded-full hover:bg-red-100 text-gray-400 hover:text-red-500 transition-all shrink-0"
                          >
                            <Trash2 size={13} />
                          </button>
                        </div>
                      ))}
                      </>
                      )}
                    </div>

                    {/* Add Account */}
                    {!isDemo && (
                    <div className="border-t border-gray-100">
                      <button
                        onClick={handleAddAccount}
                        disabled={isAddingAccount}
                        className="w-full flex items-center gap-3 px-4 py-3 text-[13px] text-gray-700 hover:bg-gray-50 transition-colors disabled:opacity-60"
                      >
                        {isAddingAccount ? (
                          <div className="w-5 h-5 border-2 border-gray-300 border-t-blue-500 rounded-full animate-spin"></div>
                        ) : (
                          <div className="w-5 h-5 rounded-full bg-gray-100 flex items-center justify-center">
                            <Plus size={12} className="text-gray-600" />
                          </div>
                        )}
                        {isAddingAccount ? 'Opening Google sign-in...' : 'Add another account'}
                      </button>
                    </div>
                    )}

                    {/* Sign out / exit demo */}
                    <div className="border-t border-gray-100">
                      {isDemo ? (
                        <button
                          onClick={handleSignOutClick}
                          className="w-full flex items-center gap-3 px-4 py-3 text-[13px] text-gray-700 hover:bg-amber-50 transition-colors"
                        >
                          <LogOut size={15} className="text-amber-600" />
                          Exit guest demo
                        </button>
                      ) : (
                        <>
                          <button
                            onClick={handleSignOutClick}
                            className="w-full flex items-center gap-3 px-4 py-3 text-[13px] text-gray-700 hover:bg-gray-50 transition-colors"
                          >
                            <LogOut size={15} className="text-gray-400" />
                            Sign out
                          </button>
                          <button
                            onClick={() => handleRemoveAccount(userProfile?.email)}
                            className="w-full flex items-center gap-3 px-4 py-2.5 text-[12px] text-red-600 hover:bg-red-50 transition-colors border-t border-gray-100"
                          >
                            <Trash2 size={14} className="text-red-400" />
                            Remove {userProfile?.email?.split('@')[0]} permanently
                          </button>
                          <button
                            onClick={handleVerifyFiles}
                            className="w-full flex items-center gap-3 px-4 py-2.5 text-[12px] text-gray-500 hover:bg-gray-50 transition-colors border-t border-gray-100"
                          >
                            <ShieldCheck size={14} className="text-gray-400" />
                            Verify credential files
                          </button>
                        </>
                      )}
                    </div>

                    {/* Footer */}
                    <div className="px-4 py-2 bg-gray-50 border-t border-gray-100">
                      <p className="text-[11px] text-gray-400 text-center">QuMail · Quantum-Safe Email</p>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </div>
        </div>
      </nav>

      <div className="flex flex-1 pt-[88px] overflow-hidden">
        {/* Sidebar */}
        <motion.aside 
          initial={false}
          animate={{ width: isSidebarCollapsed ? 70 : 260 }}
          transition={{ duration: 0.3, ease: "easeInOut" }}
          className="bg-background flex flex-col pt-2 shrink-0 overflow-x-hidden border-r border-[#CFCFCF]/50"
        >
          <div className={`px-4 mb-6 flex items-center ${isSidebarCollapsed ? 'justify-center' : 'gap-4'}`}>
            <button 
              onClick={() => setIsSidebarCollapsed(!isSidebarCollapsed)}
              className="p-2 hover:bg-gray-200 rounded-full transition-colors text-gray-600"
            >
              <Menu size={20} />
            </button>
          </div>

          <div className="px-3 mb-6">
            <motion.button 
              whileHover={{ scale: 1.02, backgroundColor: "#E8EBEE" }}
              whileTap={{ scale: 0.98 }}
              onClick={() => openCompose()}
              className={`flex items-center bg-white shadow-sm hover:shadow-md border border-gray-100 text-gray-800 transition-all ${
                isSidebarCollapsed ? 'w-12 h-12 justify-center rounded-2xl' : 'w-fit px-6 py-4 rounded-compose gap-4'
              }`}
            >
              <Plus className="w-6 h-6 text-gray-700" strokeWidth={2.5} />
              {!isSidebarCollapsed && <span className="font-semibold text-[15px]">Compose</span>}
            </motion.button>
          </div>

          <div className="flex-1 overflow-y-auto overflow-x-hidden scrollbar-hide py-2">
            <NavItem icon={Inbox} label="Inbox" count={emails.length} active={activeTab === 'inbox'} collapsed={isSidebarCollapsed} onClick={() => {setActiveTab('inbox'); setSelectedEmail(null);}} />
            <NavItem icon={Star} label="Starred" active={activeTab === 'starred'} collapsed={isSidebarCollapsed} onClick={() => {setActiveTab('starred'); setSelectedEmail(null);}} />
            <NavItem icon={Clock} label="Snoozed" active={activeTab === 'snoozed'} count={snoozedEmails.length} collapsed={isSidebarCollapsed} onClick={() => {setActiveTab('snoozed'); setSelectedEmail(null);}} />
            <NavItem icon={SendHorizontal} label="Sent" count={sentEmails.length} active={activeTab === 'sent'} collapsed={isSidebarCollapsed} onClick={() => {setActiveTab('sent'); setSelectedEmail(null);}} />
            <NavItem icon={FileText} label="Drafts" count={draftEmails.length} active={activeTab === 'drafts'} collapsed={isSidebarCollapsed} onClick={() => {setActiveTab('drafts'); setSelectedEmail(null);}} />
            <NavItem icon={Paperclip} label="Attachments" active={activeTab === 'attachments'} collapsed={isSidebarCollapsed} onClick={() => {setActiveTab('attachments'); setSelectedEmail(null);}} />
            <NavItem icon={FolderLock} label="Document Vault" active={isVaultOpen} collapsed={isSidebarCollapsed} onClick={() => setIsVaultOpen(true)} />

            <SectionHeader 
              label="Less" 
              isExpanded={isLessExpanded} 
              onToggle={() => setIsLessExpanded(!isLessExpanded)} 
              collapsed={isSidebarCollapsed}
            />
            
            <AnimatePresence>
              {(isLessExpanded || isSidebarCollapsed) && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  className="overflow-hidden"
                >
                  <NavItem icon={AlertCircle} label="Important" active={activeTab === 'important'} count={[...emails, ...sentEmails].filter(e => e.important).length} collapsed={isSidebarCollapsed} onClick={() => {setActiveTab('important'); setSelectedEmail(null);}} />
                  <NavItem icon={Clock} label="Scheduled" active={activeTab === 'scheduled'} count={scheduledEmails.length} collapsed={isSidebarCollapsed} onClick={() => {setActiveTab('scheduled'); setSelectedEmail(null);}} />
                  <NavItem icon={Mail} label="All Mail" active={activeTab === 'all'} collapsed={isSidebarCollapsed} onClick={() => {setActiveTab('all'); setSelectedEmail(null);}} />

                  <NavItem icon={Trash2} label="Bin" active={activeTab === 'trash'} count={trashEmails.length} collapsed={isSidebarCollapsed} onClick={() => {setActiveTab('trash'); setSelectedEmail(null);}} />
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          {/* Quantum Key Vault */}
          {!isSidebarCollapsed && (
            <div className="mt-auto px-4 pb-6 pt-4 shrink-0">
              <div className="bg-gradient-to-br from-gray-900 to-black rounded-2xl p-4 shadow-lg border border-gray-800 relative overflow-hidden group">
                {/* Background effect */}
                <div className="absolute -right-4 -top-8 w-24 h-24 bg-blue-500 rounded-full mix-blend-screen filter blur-[20px] opacity-20 group-hover:opacity-30 transition-opacity"></div>
                
                <div className="flex items-center gap-2 mb-3 relative z-10">
                  <div className="w-6 h-6 rounded-full bg-blue-500/20 flex items-center justify-center border border-blue-500/30">
                    <Key size={12} className="text-blue-400" />
                  </div>
                  <span className="text-[12px] font-bold text-white tracking-wide uppercase">Key Vault</span>
                </div>
                
                <div className="flex items-end gap-1.5 relative z-10">
                  <span className="text-3xl font-black text-white leading-none">50</span>
                  <span className="text-[11px] font-medium text-gray-400 mb-1 leading-none uppercase tracking-wider">Active Keys</span>
                </div>
                
                {/* Progress bar mock */}
                <div className="mt-3 h-1 bg-gray-800 rounded-full overflow-hidden relative z-10">
                  <div className="absolute inset-y-0 left-0 w-[50%] bg-gradient-to-r from-blue-500 to-indigo-400"></div>
                </div>
              </div>
            </div>
          )}
        </motion.aside>

        {/* Middle Email List */}
        <div className="w-[320px] bg-white border-r border-[#CFCFCF] flex flex-col shrink-0 flex-1 sm:flex-none">
          <div className="h-14 flex items-center px-6 border-b border-gray-100 shrink-0">
            <h2 className="text-[20px] font-medium text-gray-800 capitalize leading-none">
              {activeTab === 'sent' ? 'Sent' : 
               activeTab === 'trash' ? 'Bin' : 
               activeTab === 'all' ? 'All Mail' : 
               activeTab}
            </h2>
            {isDemo && (
              <span className="ml-3 text-[9px] font-black uppercase tracking-[0.12em] px-2 py-1 rounded-full bg-amber-100 text-amber-700 border border-amber-200">
                Demo inbox
              </span>
            )}
            {activeTab === 'trash' && trashEmails.length > 0 && (
              <button 
                onClick={emptyTrash}
                className="ml-auto flex items-center gap-2 px-3 py-1.5 bg-red-50 text-red-600 hover:bg-red-600 hover:text-white rounded-lg transition-all text-[12px] font-bold border border-red-100"
              >
                <Trash size={14} />
                Empty Bin
              </button>
            )}
          </div>
          
          <div className="flex-1 overflow-y-auto scrollbar-thin">
            {gmailError && !isDemo && (
              <div className="m-3 flex items-start gap-2 rounded-xl bg-red-50 border border-red-100 px-3 py-2.5 text-[11.5px] text-red-700">
                <AlertCircle size={14} className="mt-0.5 shrink-0" />
                <div className="flex-1">
                  <p className="font-semibold">Gmail unavailable</p>
                  <p className="mt-0.5 leading-relaxed">{gmailError}</p>
                  <button
                    onClick={handleRefresh}
                    className="mt-1.5 font-bold underline hover:no-underline"
                  >
                    Retry
                  </button>
                </div>
              </div>
            )}
            {(() => {
              let displayList = [];
              if (activeTab === 'inbox') displayList = emails;
              else if (activeTab === 'sent') displayList = sentEmails;
              else if (activeTab === 'starred') displayList = [...emails, ...sentEmails].filter(e => e.starred);
              else if (activeTab === 'drafts') displayList = draftEmails;
              else if (activeTab === 'trash') displayList = trashEmails;
              else if (activeTab === 'snoozed') displayList = snoozedEmails;
              else if (activeTab === 'important') displayList = [...emails, ...sentEmails].filter(e => e.important);
              else if (activeTab === 'attachments') displayList = [...emails, ...sentEmails].filter(e => e.attachments && e.attachments.length > 0);
              else if (activeTab === 'all') displayList = [...emails, ...sentEmails, ...draftEmails, ...snoozedEmails, ...scheduledEmails];

              // Filter blocked and search
              displayList = displayList.filter(e => !blockedSenders.includes(e.sender));
              if (searchQuery) {
                const q = searchQuery.toLowerCase();
                displayList = displayList.filter(e => 
                  e.subject.toLowerCase().includes(q) || 
                  e.sender.toLowerCase().includes(q) || 
                  e.preview.toLowerCase().includes(q)
                );
              }

              if (displayList.length === 0) {
                return (
                  <div className="h-full flex flex-col items-center justify-center text-gray-400 p-8 text-center animate-fade-in">
                    <div className="w-16 h-16 rounded-full bg-gray-50 flex items-center justify-center mb-4 transition-transform hover:scale-110">
                      <Mail className="w-8 h-8 opacity-40 text-gray-400" />
                    </div>
                    <p className="text-[15px] font-medium text-gray-500">No {activeTab} messages.</p>
                    <p className="text-[12px] opacity-70 mt-1">Click Sync icon on top right to fetch.</p>
                  </div>
                );
              }

              return (
                <>
                  {displayList.map(email => (
                <motion.div 
                  initial={{ opacity: 0, x: -10 }}
                  animate={{ opacity: 1, x: 0 }}
                  key={email.id} 
                  onClick={() => handleSelectEmail(email)}
                  className={`relative group px-4 py-4 border-b border-gray-100 cursor-pointer transition-all ${
                    selectedEmail?.id === email.id 
                    ? 'shadow-[inset_4px_0_0_0_#1A73E8] bg-blue-50' 
                    : email.read 
                      ? 'bg-green-100/50 hover:bg-green-100/70' 
                      : 'bg-red-100/50 hover:bg-red-100/70'
                  }`}
                >
                  <div className="flex gap-3">
                    <div className="flex flex-col gap-1 items-center mt-0.5">
                      <div onClick={(e) => toggleStar(e, email.id, emails.some(m => m.id === email.id))}>
                         <Star className={`w-[18px] h-[18px] cursor-pointer transition-all ${email.starred ? 'text-[#F4B400] fill-[#F4B400]' : 'text-gray-300 group-hover:text-gray-400'}`} />
                      </div>
                      <div onClick={(e) => toggleImportant(e, email.id)}>
                         <Lock className={`w-3.5 h-3.5 cursor-pointer transition-all ${email.important ? 'text-blue-600 fill-blue-600' : 'text-gray-300 group-hover:text-gray-400'}`} />
                      </div>
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex justify-between items-start mb-1 group relative">
                        <span className={`text-[14px] truncate pr-2 ${selectedEmail?.id === email.id ? 'text-blue-700 font-bold' : 'text-gray-900 font-semibold'}`}>
                          {(emails.some(m => m.id === email.id)) ? email.sender : `To: ${email.recipient}`}
                        </span>
                        <span className="text-[11px] text-gray-500 whitespace-nowrap mt-0.5 shrink-0">{email.time}</span>
                        
                        {/* Hover Subject Tooltip for Unread Messages */}
                        {!email.read && (
                          <div className="fixed opacity-0 group-hover:opacity-100 bg-[#333333] text-white text-[12px] font-medium px-3 py-1.5 rounded shadow-lg pointer-events-none transition-opacity duration-200 z-[999] whitespace-nowrap pointer-events-none hidden sm:block"
                               style={{ top: '50%', left: '50%', transform: 'translate(-50%, -200%)', position: 'absolute' }}>
                            {email.subject}
                            <div className="absolute -bottom-[4px] left-1/2 -translate-x-1/2 w-2 h-2 bg-[#333333] rotate-45"></div>
                          </div>
                        )}
                      </div>
                      <h4 className="text-[13px] font-medium text-gray-700 truncate mb-1">{email.subject}</h4>
                      <p className="text-[12px] text-gray-500 line-clamp-2 leading-relaxed">{email.preview}</p>
                      
                      <div className="flex items-center gap-2 mt-2">
                        <span className={`px-2 py-0.5 rounded-full text-[9px] font-black uppercase tracking-widest ${
                          email.demo ? 'bg-blue-100/80 text-blue-700 border border-blue-200' :
                          email.level === 4 ? 'bg-purple-100/80 text-purple-700 border border-purple-200' : 
                          email.level === 3 ? 'bg-blue-100/80 text-blue-700 border border-blue-200' : 
                          email.level === 2 ? 'bg-green-100/80 text-green-700 border border-green-200' :
                          email.qumail ? 'bg-indigo-100/80 text-indigo-700 border border-indigo-200' :
                          'bg-gray-100 text-gray-600 border border-gray-200'
                        }`}>
                          {email.demo ? 'AES-GCM' : email.level ? `L${email.level}` : email.qumail ? 'QuMail' : 'Gmail'}
                        </span>
                      </div>
                    </div>
                  </div>
                </motion.div>
                  ))}
              {(activeTab === 'inbox' || activeTab === 'sent') && gmailNextTokens[activeTab] && !isDemo && (
                <div className="p-4 flex justify-center">
                  <button
                    onClick={loadOlderMessages}
                    disabled={isLoadingOlder}
                    className="px-4 py-2 rounded-full border border-gray-200 text-[12px] font-bold text-gray-600 hover:bg-gray-50 disabled:opacity-50"
                  >
                    {isLoadingOlder ? 'Loading older messages…' : 'Load older messages'}
                  </button>
                </div>
              )}
                </>
              );
            })()}
          </div>
        </div>

        {/* Email Content Panel */}
        <div className="flex-1 bg-white overflow-hidden flex flex-col min-w-0">
          <AnimatePresence mode="wait">
            {selectedEmail ? (
              <motion.div 
                key={selectedEmail.id}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                className="flex flex-1 flex-col overflow-y-auto scrollbar-thin pb-20"
              >
                <div className="h-14 flex items-center justify-between px-8 border-b border-gray-100 sticky top-0 bg-white z-10 shrink-0">
                  <div className="flex items-center gap-2">
                    <button onClick={() => setSelectedEmail(null)} className="p-2 hover:bg-gray-100 rounded-full sm:hidden mr-2"><RotateCcw size={18}/></button>
                    <button 
                      onClick={() => moveToTrash(selectedEmail.id)} 
                      className="p-2 hover:bg-gray-100 rounded-full" 
                      title="Delete"
                    >
                      <Trash2 size={18} className="text-gray-500 hover:text-red-500" />
                    </button>
                    <button 
                      onClick={() => moveToSnoozed(selectedEmail.id)}
                      className="p-2 hover:bg-gray-100 rounded-full" 
                      title="Snooze"
                    >
                      <Clock size={18} className="text-gray-500" />
                    </button>

                    <div className="w-[1px] h-6 bg-gray-200 mx-2"></div>
                    <button 
                      onClick={(e) => toggleRead(e, selectedEmail.id)}
                      className="p-2 hover:bg-gray-100 rounded-full" 
                      title={selectedEmail.read ? "Mark as unread" : "Mark as read"}
                    >
                      <Mail size={18} className="text-gray-500" />
                    </button>
                  </div>
                  <div className="flex items-center gap-1">
                    <span className="text-[12px] text-gray-500 mr-4">1 of 1</span>
                    <button className="p-2 hover:bg-gray-100 rounded-full text-gray-400 disabled:opacity-30"><RotateCcw size={16} className="-rotate-90"/></button>
                    <button className="p-2 hover:bg-gray-100 rounded-full text-gray-400 disabled:opacity-30"><RotateCw size={16} className="rotate-90"/></button>
                    
                    {/* MORE MENU (3 DOTS) */}
                    <div className="relative">
                      <button 
                        onClick={() => setIsMoreMenuOpen(!isMoreMenuOpen)}
                        className={`p-2 hover:bg-gray-100 rounded-full transition-colors ${isMoreMenuOpen ? 'bg-gray-100' : ''}`}
                      >
                        <MoreVertical size={18} className="text-gray-500" />
                      </button>
                      
                      <AnimatePresence>
                        {isMoreMenuOpen && (
                          <>
                            <div className="fixed inset-0 z-40" onClick={() => setIsMoreMenuOpen(false)}></div>
                            <motion.div
                              initial={{ opacity: 0, scale: 0.95, y: 10 }}
                              animate={{ opacity: 1, scale: 1, y: 0 }}
                              exit={{ opacity: 0, scale: 0.95, y: 10 }}
                              className="absolute right-0 mt-2 w-56 bg-white rounded-lg shadow-xl border border-gray-200 py-2 z-50 overflow-hidden"
                            >
                              {activeTab === 'sent' ? (
                                // SENT MENU OPTIONS
                                <>
                                  <MenuOption label="Reply" onClick={() => { openCompose({ recipient: selectedEmail.recipient }); setIsMoreMenuOpen(false); }} />
                                  <MenuOption label="Forward" onClick={() => { openCompose({ message: `---------- Forwarded message ---------\nFrom: ${selectedEmail.sender}\nSubject: ${selectedEmail.subject}\n\n${selectedEmail.preview}` }); setIsMoreMenuOpen(false); }} />
                                  <MenuOption label="Delete" onClick={() => { moveToTrash(selectedEmail.id); setIsMoreMenuOpen(false); }} />
                                  <MenuOption label="Mark as unread" onClick={() => { toggleRead(null, selectedEmail.id, false); setIsMoreMenuOpen(false); }} />
                                  <div className="h-[1px] bg-gray-100 my-1"></div>

                                  <MenuOption label="Filter messages like this" onClick={() => { setSearchQuery(selectedEmail.recipient); setIsMoreMenuOpen(false); }} />
                                  <MenuOption label="Translate" onClick={() => { alert("Translating message..."); setIsMoreMenuOpen(false); }} />
                                  <MenuOption label="Print" onClick={() => { window.print(); setIsMoreMenuOpen(false); }} />
                                  <MenuOption label="Download message" onClick={() => { 
                                     const blob = new Blob([`Subject: ${selectedEmail.subject}\nFrom: ${selectedEmail.sender}\nTo: ${selectedEmail.recipient}\n\n${selectedEmail.preview}`], {type: 'text/plain'});
                                     const url = URL.createObjectURL(blob);
                                     const a = document.createElement('a');
                                     a.href = url;
                                     a.download = `email_${selectedEmail.id}.txt`;
                                     a.click();
                                     setIsMoreMenuOpen(false);
                                  }} />
                                </>
                              ) : (
                                // INBOX MENU OPTIONS
                                <>
                                  <MenuOption label="Reply" onClick={() => { openCompose({ recipient: selectedEmail.sender }); setIsMoreMenuOpen(false); }} />
                                  <MenuOption label="Forward" onClick={() => { openCompose({ message: `---------- Forwarded message ---------\nFrom: ${selectedEmail.sender}\nSubject: ${selectedEmail.subject}\n\n${selectedEmail.preview}` }); setIsMoreMenuOpen(false); }} />
                                  <MenuOption label="Delete" onClick={() => { moveToTrash(selectedEmail.id); setIsMoreMenuOpen(false); }} />
                                  <MenuOption label="Mark as unread" onClick={() => { toggleRead(null, selectedEmail.id, false); setIsMoreMenuOpen(false); }} />
                                  <div className="h-[1px] bg-gray-100 my-1"></div>
                                  <MenuOption label="Block sender" onClick={() => { setBlockedSenders(prev => [...prev, selectedEmail.sender]); setSelectedEmail(null); setIsMoreMenuOpen(false); }} />

                                  <MenuOption label="Filter messages like this" onClick={() => { setSearchQuery(selectedEmail.sender); setIsMoreMenuOpen(false); }} />
                                  <div className="h-[1px] bg-gray-100 my-1"></div>
                                  <MenuOption label="Print" onClick={() => { window.print(); setIsMoreMenuOpen(false); }} />
                                  <MenuOption label="Download message" onClick={() => { 
                                     const blob = new Blob([`Subject: ${selectedEmail.subject}\nFrom: ${selectedEmail.sender}\nTo: ${userProfile?.email}\n\n${selectedEmail.preview}`], {type: 'text/plain'});
                                     const url = URL.createObjectURL(blob);
                                     const a = document.createElement('a');
                                     a.href = url;
                                     a.download = `email_${selectedEmail.id}.txt`;
                                     a.click();
                                     setIsMoreMenuOpen(false);
                                  }} />
                                </>
                              )}
                            </motion.div>
                          </>
                        )}
                      </AnimatePresence>
                    </div>
                  </div>
                </div>

                <div className="p-8 max-w-4xl mx-auto w-full">
                  <div className="flex items-start justify-between mb-8">
                    <div>
                      <h2 className="text-[24px] font-medium text-gray-900 mb-6 leading-tight">{selectedEmail.subject}</h2>
                      <div className="flex items-center gap-4">
                        <div className="w-12 h-12 rounded-full bg-blue-100 flex items-center justify-center text-blue-700 font-bold text-lg shadow-sm border border-blue-200 shrink-0">
                          {activeTab === 'inbox' || activeTab === 'trash' ? (selectedEmail.sender ? selectedEmail.sender.charAt(0).toUpperCase() : 'U') : (selectedEmail.recipient ? selectedEmail.recipient.charAt(0).toUpperCase() : 'M')}
                        </div>
                        <div className="min-w-0">
                          <div className="flex items-baseline gap-2">
                            <p className="font-bold text-gray-900 text-[15px] truncate">
                              {(activeTab === 'inbox' || activeTab === 'trash')
                                ? (selectedEmail.senderName || selectedEmail.sender)
                                : `To: ${selectedEmail.recipient}`}
                            </p>
                            <span className="text-[12px] text-gray-500 font-normal truncate">
                              &lt;{(activeTab === 'inbox' || activeTab === 'trash')
                                ? (selectedEmail.senderEmail || selectedEmail.sender)
                                : selectedEmail.recipient}&gt;
                            </span>
                          </div>
                          <p className="text-[13px] text-gray-500 mt-0.5 flex items-center gap-1.5">
                            to me 
                            <ChevronDown size={12} className="cursor-pointer" />
                            <span className="mx-1">•</span> 
                            {selectedEmail.demo ? 'AES-256-GCM · demo session'
                              : selectedEmail.level === 1 ? 'Shielded OTP'
                              : selectedEmail.level === 3 ? 'Post-Quantum (ML-KEM / fallback)'
                              : selectedEmail.level === 4 ? 'DH key exchange (not post-quantum)'
                              : selectedEmail.level === 2 ? 'QAES (AES-256-GCM)'
                              : selectedEmail.qumail ? 'QuMail encrypted payload'
                              : 'Standard Gmail · no QuMail encryption'}
                          </p>
                        </div>
                      </div>
                    </div>
                    <div className="flex flex-col items-end gap-2">
                      <span className="text-[13px] text-gray-500">{selectedEmail.time}</span>
                      <div className="flex gap-2">
                        <button
                          className="p-2 hover:bg-gray-100 rounded-full"
                          title="Reply"
                          onClick={() => openCompose({ recipient: activeTab === 'sent' ? selectedEmail.recipient : (selectedEmail.senderEmail || selectedEmail.sender) })}
                        >
                          <RotateCcw size={18} className="text-gray-500"/>
                        </button>
                        <button className="p-2 hover:bg-gray-100 rounded-full" title="More"><MoreVertical size={18} className="text-gray-500"/></button>
                      </div>
                    </div>
                  </div>
                  
                  {selectedEmail.detail_loading ? (
                    <div className="flex items-center gap-3 text-[13px] text-gray-500 pl-16 py-8">
                      <div className="w-5 h-5 border-2 border-gray-200 border-t-blue-600 rounded-full animate-spin" />
                      Loading message…
                    </div>
                  ) : selectedEmail.detail_error ? (
                    <div className="ml-0 sm:ml-16 flex items-start gap-2 rounded-xl bg-red-50 border border-red-100 px-4 py-3 text-[12.5px] text-red-700">
                      <AlertCircle size={15} className="mt-0.5 shrink-0" />
                      <span>{selectedEmail.detail_error}</span>
                    </div>
                  ) : selectedEmail.qumail && !selectedEmail.qumail_decrypted ? (
                    <div className="text-[#3c4043] whitespace-pre-wrap leading-[1.6] text-[15px] pl-16">
                      {selectedEmail.decrypted_text || selectedEmail.preview}
                    </div>
                  ) : selectedEmail.qumail_decrypted ? (
                    <div className="text-[#3c4043] whitespace-pre-wrap leading-[1.6] text-[15px] pl-16">
                      {selectedEmail.decrypted_text}
                    </div>
                  ) : selectedEmail.body_html ? (
                    <div
                      className="email-html text-[#3c4043] leading-[1.6] text-[15px] pl-16"
                      // Rendered only after server-side bleach sanitisation.
                      dangerouslySetInnerHTML={{ __html: selectedEmail.body_html }}
                    />
                  ) : (
                    <div className="text-[#3c4043] whitespace-pre-wrap leading-[1.6] text-[15px] pl-16">
                      {selectedEmail.decrypted_text || selectedEmail.body_text || selectedEmail.preview}
                    </div>
                  )}

                  {selectedEmail.qumail && !selectedEmail.demo && (
                    <div className="mt-6 ml-0 sm:ml-16 rounded-xl border border-indigo-100 bg-indigo-50/50 p-4">
                      <div className="flex items-center justify-between gap-3 flex-wrap">
                        <div className="flex items-start gap-2">
                          <ShieldCheck size={16} className="text-indigo-600 mt-0.5 shrink-0" />
                          <div>
                            <p className="text-[13px] font-bold text-indigo-900">QuMail encrypted message</p>
                            <p className="text-[11.5px] text-indigo-700/80 mt-0.5">
                              {selectedEmail.qumail_decrypted
                                ? 'Decrypted with the keys stored on this backend.'
                                : 'The message body is inside an encrypted attachment. Decryption happens on the server; keys never reach the browser.'}
                            </p>
                          </div>
                        </div>
                        {!selectedEmail.qumail_decrypted && (
                          <button
                            onClick={handleDecryptSelected}
                            disabled={decrypting}
                            className="px-4 py-2 rounded-full bg-indigo-600 text-white text-[12px] font-bold hover:bg-indigo-700 disabled:opacity-50"
                          >
                            {decrypting ? 'Decrypting…' : 'Decrypt message'}
                          </button>
                        )}
                      </div>
                      {decryptError && (
                        <p className="mt-2 text-[12px] text-red-600 flex items-start gap-1.5">
                          <AlertCircle size={13} className="mt-0.5 shrink-0" /> {decryptError}
                        </p>
                      )}
                      {selectedEmail.decrypted_attachments?.length > 0 && (
                        <p className="mt-2 text-[12px] text-indigo-800">
                          {selectedEmail.decrypted_attachments.length} decrypted attachment(s) are listed below.
                        </p>
                      )}
                    </div>
                  )}

                  {selectedEmail.demo && (
                    <div className="mt-6 ml-0 sm:ml-16 flex items-start gap-2 rounded-xl bg-amber-50 border border-amber-100 px-4 py-3 text-[12px] text-amber-800">
                      <Info size={14} className="mt-0.5 shrink-0" />
                      <span>Demo only — this message lives in an in-memory guest session and was never sent to Gmail.</span>
                    </div>
                  )}

                  {detailEncryption && (
                    <div className="mt-4 ml-0 sm:ml-16 rounded-xl border border-gray-100 bg-[#FAFBFC] p-4">
                      <p className="text-[10px] font-black uppercase tracking-[0.14em] text-gray-400 mb-2">
                        Encryption metadata
                      </p>
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-2">
                        <MetaRow label="Algorithm" value={detailEncryption.algorithm} />
                        <MetaRow label="AAD" value={detailEncryption.aad} />
                        <MetaRow
                          label="Nonce (b64)"
                          value={detailEncryption.nonce_b64 ? `${detailEncryption.nonce_b64.slice(0, 14)}…` : ''}
                        />
                        <MetaRow label="Key scope" value={detailEncryption.key_scope} />
                      </div>
                    </div>
                  )}

                  {/* Attachments Section */}
                  {detailAttachments.length > 0 && (
                    <div className="mt-12 pl-16 pt-8 border-t border-gray-100">
                      <div className="flex items-center justify-between mb-4">
                        <h4 className="text-[14px] font-bold text-gray-800 flex items-center gap-2">
                          <Paperclip size={16} /> 
                          {detailAttachments.length} Attachments
                        </h4>
                      </div>
                      
                      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                        {detailAttachments.map((att, idx) => (
                           <motion.div 
                            key={idx}
                            whileHover={{ y: -2 }}
                            className="group relative border border-gray-200 rounded-xl overflow-hidden bg-gray-50 hover:bg-white hover:shadow-lg transition-all"
                           >
                              <div className="aspect-[4/3] flex items-center justify-center bg-white/50">
                                 {att.name?.match(/\.(png|jpg|jpeg|gif|webp)$/i) ? (
                                    <Image className="w-10 h-10 text-blue-400 opacity-40" />
                                 ) : (
                                    <FileText className="w-10 h-10 text-gray-300 opacity-40" />
                                 )}
                              </div>
                              <div className="p-3 bg-white border-t border-gray-100">
                                <div className="flex items-center justify-between">
                                  <div className="min-w-0 pr-2">
                                    <p className="text-[13px] font-semibold text-gray-800 truncate">{att.name}</p>
                                    <p className="text-[11px] text-gray-500">{(att.size / 1024 || 0).toFixed(1)} KB</p>
                                  </div>
                                  <button 
                                    onClick={() => {
                                      if (att.encryption && !att.file_data_b64 && !att.download_url) {
                                        alert(
                                          `Encrypted attachment\n\nAlgorithm: ${att.encryption.algorithm || 'AES-256-GCM'}\nAAD: ${att.encryption.aad || '—'}\nSHA-256: ${att.sha256 || 'n/a'}\n\nDemo attachments are held only as ciphertext in the guest session.`
                                        );
                                        return;
                                      }
                                      if (att.file_data_b64) {
                                        // In-memory base64 — direct download
                                        const byteChars = atob(att.file_data_b64);
                                        const byteArr = new Uint8Array(byteChars.length);
                                        for (let i = 0; i < byteChars.length; i++) byteArr[i] = byteChars.charCodeAt(i);
                                        const blob = new Blob([byteArr]);
                                        const url = URL.createObjectURL(blob);
                                        const a = document.createElement('a');
                                        a.href = url;
                                        a.download = att.name || 'attachment';
                                        a.click();
                                        URL.revokeObjectURL(url);
                                      } else if (att.download_url) {
                                        // Authorized backend route; token stays server-side
                                        fetch(`${API_BASE}${att.download_url}`)
                                          .then(r => {
                                            if (!r.ok) throw new Error('Download failed');
                                            return r.blob();
                                          })
                                          .then(blob => {
                                            const url = URL.createObjectURL(blob);
                                            const a = document.createElement('a');
                                            a.href = url;
                                            a.download = att.name || 'attachment';
                                            a.click();
                                            URL.revokeObjectURL(url);
                                          })
                                          .catch(() => alert('Download failed. The attachment may be unavailable or too large.'));
                                      } else {
                                        alert('This decrypted attachment is too large to download in the browser. Its size is ' + ((att.size / 1024) || 0).toFixed(1) + ' KB.');
                                      }
                                    }}
                                    className="p-2 bg-gray-100 hover:bg-blue-600 hover:text-white rounded-lg transition-colors text-gray-600"
                                  >
                                    <Download size={16} />
                                  </button>
                                </div>
                              </div>
                           </motion.div>
                        ))}
                      </div>
                    </div>
                  )}

                  <div className="mt-12 pl-16 flex gap-4">
                    <button
                      onClick={() => openCompose({ recipient: activeTab === 'sent' ? selectedEmail.recipient : (selectedEmail.senderEmail || selectedEmail.sender) })}
                      className="flex items-center gap-2 px-6 py-2 border border-gray-300 rounded-full text-[14px] font-medium text-gray-600 hover:bg-gray-50 transition-colors"
                    >
                      <RotateCcw size={16} /> Reply
                    </button>
                    <button
                      onClick={() => openCompose({ message: `---------- Forwarded message ---------\nFrom: ${selectedEmail.sender}\nSubject: ${selectedEmail.subject}\n\n${selectedEmail.decrypted_text || selectedEmail.preview}` })}
                      className="flex items-center gap-2 px-6 py-2 border border-gray-300 rounded-full text-[14px] font-medium text-gray-600 hover:bg-gray-50 transition-colors"
                    >
                      <SendHorizontal size={16} /> Forward
                    </button>
                  </div>
                </div>
              </motion.div>
            ) : (
              <motion.div 
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="h-full flex flex-col items-center justify-center p-12 text-center"
              >
                <div className="relative mb-8">
                  <div className="absolute inset-0 bg-blue-100 rounded-full blur-2xl opacity-50 animate-pulse"></div>
                  <img src="/logo.png" alt="QuMail Logo" className="relative w-[140px] h-[140px] object-contain drop-shadow-xl mix-blend-multiply z-10" />
                </div>
                <h3 className="text-[22px] font-bold text-gray-800 mb-2">Select an item to read</h3>
                <p className="text-[15px] text-gray-500 max-w-[280px]">Nothing is selected. Pick an email from the left list to view its secure content.</p>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>

      
      {/* COMPOSE MODAL */}
      <AnimatePresence>
        {isComposeOpen && (
          <motion.div 
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 bg-black/20 backdrop-blur-[2px] z-[100] flex items-center justify-center p-4"
          >
            <motion.div 
              initial={{ scale: 0.95, y: 20 }}
              animate={{ scale: 1, y: 0 }}
              exit={{ scale: 0.95, y: 20 }}
              className="bg-white rounded-xl shadow-2xl w-full max-w-[720px] h-[640px] flex flex-col overflow-hidden border border-gray-200 relative"
            >
              {/* Processing Overlay */}
              <AnimatePresence>
                {isProcessing && (
                  <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    className="absolute inset-0 bg-white/60 backdrop-blur-[1px] z-[120] flex flex-col items-center justify-center"
                  >
                    <div className="w-16 h-16 border-4 border-blue-100 border-t-blue-600 rounded-full animate-spin mb-4"></div>
                    <h3 className="text-[18px] font-bold text-gray-900">
                      {isDemo || securityLevel > 0 ? 'Encrypting Content...' : 'Sending Message...'}
                    </h3>
                    <p className="text-[14px] text-gray-500 mt-1">
                      {isDemo || securityLevel > 0 ? 'Applying QuMail protection' : 'Submitting to Gmail'}
                    </p>
                  </motion.div>
                )}
              </AnimatePresence>
              {/* Header */}
              <div className="bg-[#F1F3F4] px-4 py-2 flex items-center justify-between shrink-0">
                <span className="text-[14px] font-bold text-gray-700">New Message</span>
                <div className="flex items-center gap-1">
                  <button className="p-1.5 hover:bg-gray-200 rounded text-gray-500"><Minimize2 size={14}/></button>
                  <button className="p-1.5 hover:bg-gray-200 rounded text-gray-500"><Maximize2 size={14}/></button>
                  <button onClick={() => setIsComposeOpen(false)} className="p-1.5 hover:bg-gray-200 rounded text-gray-500"><X size={14}/></button>
                </div>
              </div>

              {isDemo && (
                <div className="px-4 py-2 bg-amber-50 border-b border-amber-100 flex items-start gap-2 text-[11.5px] text-amber-800 shrink-0">
                  <Info size={13} className="mt-0.5 shrink-0" />
                  <span>
                    Guest demo — the body and attachments are encrypted with a per-session AES-256-GCM
                    key. <strong>Nothing is sent to Gmail.</strong>
                  </span>
                </div>
              )}

              {!isDemo && securityLevel === 0 && (
                <div className="px-4 py-2 bg-gray-50 border-b border-gray-100 flex items-start gap-2 text-[11.5px] text-gray-600 shrink-0">
                  <Info size={13} className="mt-0.5 shrink-0" />
                  <span>
                    Standard Gmail is selected. This message will <strong>not</strong> have QuMail
                    end-to-end encryption.
                  </span>
                </div>
              )}

              {!isDemo && securityLevel > 0 && (
                <div className="px-4 py-2 bg-green-50 border-b border-green-100 flex items-start gap-2 text-[11.5px] text-green-800 shrink-0">
                  <ShieldCheck size={13} className="mt-0.5 shrink-0" />
                  <span>
                    QuMail L{securityLevel} selected. The body and attachments are encrypted before
                    Gmail receives them.
                  </span>
                </div>
              )}

              {sendStatus && (
                <div className={`mx-4 mt-3 flex items-start gap-2 rounded-xl px-3.5 py-2.5 text-[12px] shrink-0 ${
                  sendStatus.type === 'success'
                    ? 'bg-green-50 border border-green-100 text-green-800'
                    : 'bg-red-50 border border-red-100 text-red-700'
                }`}>
                  {sendStatus.type === 'success' ? (
                    <ShieldCheck size={15} className="mt-0.5 shrink-0" />
                  ) : (
                    <AlertCircle size={15} className="mt-0.5 shrink-0" />
                  )}
                  <span className="leading-relaxed flex-1">{sendStatus.text}</span>
                  <button onClick={() => setSendStatus(null)} className="p-0.5 hover:opacity-70">
                    <X size={13} />
                  </button>
                </div>
              )}

              {/* Fields */}
              <div className="px-4 py-1 flex flex-col divide-y divide-[#E0E0E0]">
                <div className="flex items-center py-2.5">
                  <span className="text-[14px] text-gray-500 w-12">To</span>
                  <input 
                    type="email" 
                    value={recipient} 
                    onChange={(e) => setRecipient(e.target.value)}
                    className="flex-1 outline-none text-[14px] text-gray-800"
                  />
                  <div className="flex gap-3 text-[12px] text-gray-500 font-medium">
                    <button className="hover:underline">Cc</button>
                    <button className="hover:underline">Bcc</button>
                  </div>
                </div>
                <div className="flex items-center py-2.5">
                  <input 
                    type="text" 
                    placeholder="Subject"
                    value={subject}
                    onChange={(e) => setSubject(e.target.value)}
                    className="flex-1 outline-none text-[14px] text-gray-800"
                  />
                </div>
              </div>

              {/* Editor */}
              <div className="flex-1 p-4 overflow-y-auto min-h-[300px]">
                <textarea 
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  placeholder="QuMail: Secure encryption begins here..."
                  className="w-full h-full outline-none resize-none text-[14px] text-[#3c4043] font-medium leading-[1.6]"
                />
              </div>

              {/* Attachments Preview */}
              {attachedFiles.length > 0 && (
                <div className="px-4 py-2 border-t border-gray-50 flex flex-wrap gap-2 max-h-48 overflow-y-auto bg-gray-50/50">
                  {attachedFiles.map((f, idx) => (
                    <div key={f.id} className="flex items-center gap-2 bg-white border border-gray-200 rounded-lg p-1.5 pr-3 group shadow-sm transition-all hover:border-blue-200 min-w-[200px]">
                      {/* Image Thumbnail or Icon */}
                      {f.previewUrl ? (
                        <div className="w-10 h-10 rounded border border-gray-100 overflow-hidden shrink-0">
                          <img src={f.previewUrl} alt="preview" className="w-full h-full object-cover" />
                        </div>
                      ) : (
                        <div className="w-10 h-10 rounded bg-gray-100 flex items-center justify-center shrink-0">
                          <FileText size={20} className="text-gray-400" />
                        </div>
                      )}

                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5">
                          <span className="text-[11px] font-bold text-gray-700 truncate">{f.name}</span>
                          {f.status === 'secured' && <ShieldCheck size={12} className="text-green-500" title="Secured" />}
                        </div>
                        <div className="flex items-center justify-between mt-0.5">
                          <span className="text-[9px] text-gray-400">{(f.size / 1024).toFixed(0)} KB</span>
                          
                          {/* Status Indicators */}
                          {f.status === 'uploading' && (
                            <div className="flex items-center gap-1">
                              <div className="w-2 h-2 border-2 border-gray-100 border-t-gray-400 rounded-full animate-spin"></div>
                              <span className="text-[9px] text-gray-500 font-medium italic">Uploading...</span>
                            </div>
                          )}
                          {f.status === 'attached' && (
                            <div className="flex items-center gap-1">
                              <Shield size={10} className="text-gray-400" />
                              <span className="text-[9px] text-gray-600 font-bold">Ready</span>
                            </div>
                          )}
                          {f.status === 'encrypting' && (
                            <div className="flex items-center gap-1">
                              <div className="w-2.5 h-2.5 border-2 border-blue-100 border-t-blue-500 rounded-full animate-spin"></div>
                              <span className="text-[9px] text-blue-500 font-bold">Securing...</span>
                            </div>
                          )}
                          {f.status === 'error' && (
                            <div className="flex items-center gap-1.5">
                              <span className="text-[9px] text-red-500 font-bold">Failed</span>
                            </div>
                          )}
                          {f.status === 'secured' && (
                            <span className="text-[9px] text-green-600 font-bold italic">Secured & Ready</span>
                          )}
                        </div>
                      </div>

                      <button 
                        onClick={() => {
                          if (f.previewUrl) URL.revokeObjectURL(f.previewUrl);
                          setAttachedFiles(prev => prev.filter(item => item.id !== f.id));
                        }}
                        className="text-gray-400 hover:text-red-500 transition-colors p-1"
                      >
                        <X size={16}/>
                      </button>
                    </div>
                  ))}
                </div>
              )}

              {/* Toolbar & Actions */}
              <div className="bg-white px-4 py-3 border-t border-gray-100 flex flex-col gap-3">
                {/* Formatting Toolbar */}
                <div className="bg-[#F1F3F4]/80 p-1 rounded-full flex items-center justify-between">
                  <div className="flex items-center gap-0.5 ml-1">
                    <button className="p-1.5 hover:bg-gray-200 rounded-full text-gray-600"><RotateCcw size={14}/></button>
                    <button className="p-1.5 hover:bg-gray-200 rounded-full text-gray-600"><RotateCw size={14}/></button>
                    <div className="w-[1px] h-4 bg-gray-300 mx-1"></div>
                    <button className="px-2 py-0.5 hover:bg-gray-200 rounded text-[12px] text-gray-600 font-semibold flex items-center gap-1">Sans Serif <ChevronDown size={10}/></button>
                    <button className="p-1.5 hover:bg-gray-200 rounded text-gray-600"><Type size={14}/></button>
                    <div className="w-[1px] h-4 bg-gray-300 mx-1"></div>
                    <button className="p-1.5 hover:bg-gray-200 rounded text-gray-600"><Bold size={14}/></button>
                    <button className="p-1.5 hover:bg-gray-200 rounded text-gray-600"><Italic size={14}/></button>
                    <button className="p-1.5 hover:bg-gray-200 rounded text-gray-600"><Underline size={14}/></button>
                    <button className="p-1.5 hover:bg-gray-200 rounded text-gray-600"><AlignLeft size={14}/></button>
                    <div className="w-[1px] h-4 bg-gray-300 mx-1"></div>
                    <button className="p-1.5 hover:bg-gray-200 rounded text-gray-600"><List size={14}/></button>
                    <button className="p-1.5 hover:bg-gray-200 rounded text-gray-600"><ListOrdered size={14}/></button>
                    <button className="p-1.5 hover:bg-gray-200 rounded text-gray-600"><Quote size={14}/></button>
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1 shrink-0 relative">
                    {/* Send Button & Dropdown */}
                    <div className="relative">
                      <button 
                        onClick={() => setIsSecurityDropdownOpen(!isSecurityDropdownOpen)}
                        className="bg-primary hover:bg-blue-700 text-white font-bold text-[14px] px-6 py-2.5 rounded-full flex items-center gap-3 transition-all shadow-lg hover:shadow-blue-200/50"
                      >
                        Send <ChevronDown size={16} className={`transition-transform duration-200 ${isSecurityDropdownOpen ? 'rotate-180' : ''}`} />
                      </button>

                      <AnimatePresence>
                        {isSecurityDropdownOpen && (
                          <>
                            <div className="fixed inset-0 z-[105]" onClick={() => setIsSecurityDropdownOpen(false)}></div>
                            <motion.div 
                              initial={{ opacity: 0, scale: 0.95, y: -10 }}
                              animate={{ opacity: 1, scale: 1, y: -20 }}
                              exit={{ opacity: 0, scale: 0.95, y: -10 }}
                              className="absolute bottom-full left-0 mb-4 w-[280px] bg-white/80 backdrop-blur-md rounded-2xl shadow-[0_8px_30px_rgba(0,0,0,0.12)] border border-white/50 overflow-visible z-[110]"
                            >
                              <div className="p-3 flex flex-col gap-1 relative">
                                {[
                                  { id: 0, label: 'Standard email (no encryption)', icon: '✉️', bg: 'bg-gray-100', text: 'text-gray-600', tipDetails: 'Sends an ordinary Gmail message with no QuMail encryption.\nGmail and the recipient can read the content. Choose this only when you do not need QuMail protection.' },
                                  { id: 1, label: 'OTP (One Time Pad) - Text Only', icon: '🔑', bg: 'bg-blue-100/50', text: 'text-blue-700', tipDetails: 'Strictly for text messages. Uses a secret one-time key generated locally.\nNote: Attachments are NOT supported in L1.' },
                                  { id: 2, label: 'QAES (AES-256-GCM)', icon: '🛡️', bg: 'bg-green-100/50', text: 'text-green-700', tipDetails: 'AES-256-GCM authenticated encryption, with keys drawn from the software KME pool.\nThe KME is a key-distribution demo, not real quantum key distribution (QKD).' },
                                  { id: 3, label: 'PQC (Post-Quantum ML-KEM)', icon: '🌀', bg: 'bg-purple-100/50', text: 'text-purple-700', tipDetails: 'ML-KEM-768 key encapsulation where quantcrypt is installed.\nFalls back to the KME key pool when the PQC library is unavailable.' },
                                  { id: 4, label: 'DH (Secure Key Handshake)', icon: '☁️', bg: 'bg-amber-100/50', text: 'text-amber-700', tipDetails: 'Diffie-Hellman (RFC 3526 Group 14) key exchange with the identity service.\nClassical key exchange - NOT post-quantum.' }
                                ].map(opt => (
                                  <div 
                                    key={opt.id}
                                    onMouseEnter={() => setHoveredSecurity(opt.id)}
                                    onMouseLeave={() => setHoveredSecurity(null)}
                                    onClick={(e) => {
                                      setSecurityLevel(opt.id);
                                      setIsSecurityDropdownOpen(false);
                                      handleSendMessage(e, opt.id);
                                    }}
                                    className="relative group flex items-center h-[52px] px-3 rounded-xl hover:bg-white cursor-pointer transition-all duration-200"
                                  >
                                    <div className={`w-9 h-9 rounded-full ${opt.bg} flex items-center justify-center mr-3 shadow-sm border border-white/50 shrink-0`}>
                                      <span className="text-lg leading-none filter drop-shadow-sm">{opt.icon}</span>
                                    </div>
                                    <div className="flex flex-col justify-center">
                                      <span className="text-[14px] font-bold text-gray-800 leading-tight">
                                        {opt.id === 0 ? 'Standard – No encryption' : `L${opt.id} – ${opt.label.split(' (')[0]}`}
                                      </span>
                                      <span className={`text-[11px] font-medium leading-tight ${opt.text}`}>
                                        {opt.label.includes('(') ? '(' + opt.label.split('(')[1] : ''}
                                      </span>
                                    </div>
                                    
                                    {/* Tooltip */}
                                    <AnimatePresence>
                                      {hoveredSecurity === opt.id && (
                                        <motion.div 
                                          initial={{ opacity: 0, x: 10, scale: 0.95 }}
                                          animate={{ opacity: 1, x: 0, scale: 1 }}
                                          exit={{ opacity: 0, scale: 0.95 }}
                                          transition={{ duration: 0.15, ease: "easeOut" }}
                                          className="absolute left-[calc(100%+16px)] top-1/2 -translate-y-1/2 bg-[#1A1A1A] text-white px-4 py-3 rounded-xl text-[13px] w-[260px] shadow-2xl z-[120] pointer-events-none border border-white/10"
                                        >
                                          <div className="flex items-center gap-2 mb-1.5 border-b border-white/10 pb-1.5">
                                            <span className="text-base">{opt.icon}</span>
                                            <p className="font-bold text-white tracking-wide">{opt.id === 0 ? 'Standard – No encryption' : `L${opt.id} – ${opt.label.split(' (')[0]}`}</p>
                                          </div>
                                          <p className="opacity-80 leading-relaxed whitespace-pre-line text-[12px]">{opt.tipDetails}</p>
                                          
                                          {/* Arrow */}
                                          <div className="absolute top-1/2 -translate-y-1/2 -left-1.5 w-3 h-3 bg-[#1A1A1A] rotate-45 border-l border-b border-white/10"></div>
                                        </motion.div>
                                      )}
                                    </AnimatePresence>
                                  </div>
                                ))}
                              </div>
                            </motion.div>
                          </>
                        )}
                      </AnimatePresence>
                    </div>

                    <div className="flex items-center gap-0.5 ml-2">
                      <button 
                        onClick={() => fileInputRef.current?.click()}
                        className="p-2 hover:bg-gray-100 rounded-full text-gray-500" 
                        title="Attach files"
                      >
                        <Paperclip size={18}/>
                      </button>
                      <button className="p-2 hover:bg-gray-100 rounded-full text-gray-500"><LinkIcon size={18}/></button>
                      <button className="p-2 hover:bg-gray-100 rounded-full text-gray-500"><SmileIcon size={18}/></button>
                      <button 
                        onClick={() => fileInputRef.current?.click()}
                        className="p-2 hover:bg-gray-100 rounded-full text-gray-500"
                      >
                        <Image size={18}/>
                      </button>
                      <button className="p-2 hover:bg-gray-100 rounded-full text-gray-500"><Lock size={18}/></button>
                      <button className="p-2 hover:bg-gray-100 rounded-full text-gray-500"><MoreVertical size={18}/></button>
                    </div>
                  </div>

                  <button className="p-2 hover:bg-gray-100 rounded-full text-gray-400 hover:text-red-500 transition-colors">
                    <Trash2 size={20}/>
                  </button>
                </div>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {isVaultOpen && (
          <DocumentVault onClose={() => setIsVaultOpen(false)} onAttach={handleAttachFromVault} />
        )}
      </AnimatePresence>

    </div>
  );
};

// Redesigned Components
const NavItem = ({ icon: Icon, label, count, active, collapsed, onClick }) => {
  return (
    <motion.div
      whileHover={{ scale: 1.02, backgroundColor: "rgba(201, 212, 232, 0.4)" }}
      whileTap={{ scale: 0.98 }}
      onClick={onClick}
      className={`group relative flex items-center h-10 px-3 mx-2 my-0.5 rounded-full cursor-pointer transition-colors duration-150 ${
        active ? 'bg-active text-gray-900 font-semibold' : 'text-gray-600 hover:text-gray-900'
      }`}
    >
      <div className={`flex items-center justify-center ${collapsed ? 'w-full' : 'mr-4'}`}>
        <Icon className={`w-[18px] h-[18px] ${active ? 'text-gray-900' : 'text-gray-600'}`} strokeWidth={active ? 2.5 : 2} />
      </div>
      
      {!collapsed && (
        <>
          <span className="flex-1 text-[13.5px] truncate">{label}</span>
          {count > 0 && (
            <span className={`text-[11px] font-medium ${active ? 'text-gray-900' : 'text-gray-500'}`}>
              {count}
            </span>
          )}
        </>
      )}

      {collapsed && (
        <div className="absolute left-full ml-4 px-2 py-1 bg-gray-800 text-white text-[11px] rounded opacity-0 group-hover:opacity-100 pointer-events-none transition-opacity z-50 whitespace-nowrap">
          {label}
        </div>
      )}
    </motion.div>
  );
};

const SectionHeader = ({ label, isExpanded, onToggle, collapsed }) => {
  if (collapsed) return <div className="h-4" />;
  
  return (
    <div 
      onClick={onToggle}
      className="flex items-center px-6 py-2 mt-2 text-gray-500 hover:text-gray-900 cursor-pointer group"
    >
      <ChevronDown className={`w-3.5 h-3.5 mr-2 transition-transform duration-200 ${isExpanded ? '' : '-rotate-90'}`} />
      <span className="text-[12px] font-bold uppercase tracking-wider">{label}</span>
    </div>
  );
};

const MenuOption = ({ label, onClick, danger }) => (
  <button
    onClick={onClick}
    className={`w-full text-left px-4 py-2 text-[14px] transition-colors hover:bg-gray-100 ${danger ? 'text-red-600' : 'text-gray-700'}`}
  >
    {label}
  </button>
);

const MetaRow = ({ label, value }) => (
  <div className="min-w-0">
    <p className="text-[9.5px] font-black uppercase tracking-[0.12em] text-gray-400">{label}</p>
    <p className="text-[11.5px] text-gray-700 font-medium break-words">{value || '—'}</p>
  </div>
);

export default QuMailApp;