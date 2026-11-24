'use client';

import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import {
  Activity, Bell, BookOpenCheck, BookOpenText, ChevronDown, ChevronLeft,
  ClipboardCheck, ClipboardList, FileBarChart, FileSpreadsheet, LayoutDashboard, LifeBuoy, LogOut,
  Menu, Search, Settings2, ShieldAlert, Sparkles, UserRound, UsersRound, WandSparkles, X,
} from 'lucide-react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api, clearApiCache } from '@/lib/api';

type Profile = { name: string; email: string; role: string };
type Notice = { id: number; title: string; body: string; read: boolean };
type NavigationItem = { label: string; href: string; icon: typeof LayoutDashboard };

const teacherLinks: NavigationItem[] = [
  { label: 'Dashboard', href: '/teacher/dashboard', icon: LayoutDashboard },
  { label: 'Students', href: '/teacher/students', icon: UsersRound },
  { label: 'Analytics', href: '/teacher/analytics', icon: Activity },
  { label: 'Assignments', href: '/teacher/assignments', icon: ClipboardList },
  { label: 'Early Warnings', href: '/teacher/early-warnings', icon: ShieldAlert },
  { label: 'Academic Data', href: '/teacher/academic-data', icon: FileSpreadsheet },
  { label: 'Internal Marks', href: '/teacher/internal-marks', icon: BookOpenText },
  { label: 'Risk Monitoring', href: '/teacher/risk-monitoring', icon: Activity },
  { label: 'AI Interventions', href: '/teacher/interventions', icon: WandSparkles },
  { label: 'Subject recovery', href: '/teacher/recovery', icon: ClipboardCheck },
  { label: 'Reports', href: '/teacher/reports', icon: FileBarChart },
  { label: 'Settings', href: '/teacher/settings', icon: Settings2 },
];
const studentLinks: NavigationItem[] = [
  { label: 'My progress', href: '/student/dashboard', icon: LayoutDashboard },
  { label: 'Assignments', href: '/student/assignments', icon: ClipboardList },
  { label: 'Recovery tasks', href: '/student/recovery', icon: ClipboardCheck },
  { label: 'Subjects', href: '/student/subjects', icon: BookOpenText },
  { label: 'Support', href: '/student/support', icon: Sparkles },
  { label: 'Profile', href: '/student/profile', icon: UserRound },
];

export default function PortalShell({ children, role }: { children: React.ReactNode; role: 'TEACHER' | 'STUDENT' }) {
  const pathname = usePathname();
  const router = useRouter();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [notices, setNotices] = useState<Notice[]>([]);
  const [collapsed, setCollapsed] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [noticeOpen, setNoticeOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [error, setError] = useState('');
  const reduceMotion = useReducedMotion() ?? false;
  const menuTriggerRef = useRef<HTMLButtonElement>(null);
  const mobileNavRef = useRef<HTMLElement>(null);
  const closeMobileMenuRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    let active = true;
    const token = window.localStorage.getItem('token');
    if (!token) {
      router.replace('/login');
      return () => {
        active = false;
      };
    }
    Promise.all([api<Profile>('/api/v1/me'), api<Notice[]>('/api/v1/notifications')])
      .then(([current, items]) => {
        if (!active) return;
        if (current.role !== role) {
          router.replace(current.role === 'TEACHER' ? '/teacher/dashboard' : '/student/dashboard');
          return;
        }
        setProfile(current);
        setNotices(items);
      })
      .catch((requestError: Error) => {
        if (!active) return;
        setError(requestError.message);
        clearApiCache();
        window.localStorage.removeItem('token');
        router.replace('/login');
      });
    return () => {
      active = false;
    };
  }, [role, router]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setSearchOpen(true);
        window.setTimeout(() => document.getElementById('workspace-search')?.focus(), 0);
      }
      if (event.key === 'Escape') {
        setSearchOpen(false);
        setNoticeOpen(false);
        setProfileOpen(false);
        setMenuOpen(false);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  useEffect(() => {
    if (!menuOpen) return;
    const previousFocus = menuTriggerRef.current ?? document.activeElement;
    closeMobileMenuRef.current?.focus();
    return () => {
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus();
    };
  }, [menuOpen]);

  const containMobileMenuFocus = (event: React.KeyboardEvent<HTMLElement>) => {
    if (event.key !== 'Tab') return;
    const focusable = mobileNavRef.current?.querySelectorAll<HTMLElement>(
      'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
    );
    if (!focusable?.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const focusIsOutside = !mobileNavRef.current?.contains(document.activeElement);
    if (event.shiftKey && (document.activeElement === first || focusIsOutside)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || focusIsOutside)) {
      event.preventDefault();
      first.focus();
    }
  };

  const links = role === 'TEACHER' ? teacherLinks : studentLinks;
  const searchResults = useMemo(() => {
    const query = searchTerm.trim().toLowerCase();
    return links.filter((item) => !query || item.label.toLowerCase().includes(query)).slice(0, 6);
  }, [links, searchTerm]);
  const currentTitle = links.find((item) => pathname === item.href || pathname.startsWith(`${item.href}/`))?.label
    ?? (role === 'TEACHER' ? 'Faculty workspace' : 'Student workspace');
  const logout = () => {
    clearApiCache();
    window.localStorage.removeItem('token');
    router.replace('/login');
  };
  const openRoute = (href: string) => {
    setSearchOpen(false);
    setSearchTerm('');
    setMenuOpen(false);
    router.push(href);
  };

  const navigation = (mobile = false) => (
    <nav aria-label={mobile ? 'Mobile primary navigation' : 'Primary navigation'} className="space-y-1">
      {links.map(({ label, href, icon: Icon }) => {
        const active = pathname === href || pathname.startsWith(`${href}/`);
        return (
          <Link
            key={href}
            href={href}
            prefetch={false}
            onMouseEnter={() => router.prefetch(href)}
            onFocus={() => router.prefetch(href)}
            onClick={() => mobile && setMenuOpen(false)}
            title={collapsed && !mobile ? label : undefined}
            aria-current={active ? 'page' : undefined}
            className={`group relative flex min-h-10 items-center gap-3 rounded-lg px-3 text-[13px] transition-colors focus-visible:outline-none ${active ? 'bg-white/[.07] text-white' : 'text-slate-400 hover:bg-white/[.045] hover:text-slate-100'} ${collapsed && !mobile ? 'justify-center px-0' : ''}`}
          >
            {active && <span aria-hidden="true" className="absolute bottom-2 left-0 top-2 w-[2px] rounded-r bg-rose-500 shadow-[0_0_12px_rgba(255,51,71,.65)]" />}
            <Icon size={17} aria-hidden="true" className={`shrink-0 ${active ? 'text-rose-300' : 'text-slate-500 group-hover:text-slate-200'}`} />
            {(!collapsed || mobile) && <span className="truncate">{label}</span>}
            {active && (!collapsed || mobile) && <span aria-hidden="true" className="ml-auto size-1.5 rounded-full bg-rose-400" />}
          </Link>
        );
      })}
    </nav>
  );

  return (
    <div className="min-h-screen text-[var(--text)]">
      <div className="flex min-h-screen">
        <aside className={`sticky top-0 hidden h-screen shrink-0 flex-col border-r border-white/[.07] bg-[#111214]/95 px-3 py-5 transition-[width] duration-300 lg:flex ${collapsed ? 'w-[76px]' : 'w-[252px]'}`}>
          <Link href={role === 'TEACHER' ? '/teacher/dashboard' : '/student/dashboard'} className={`mb-9 flex min-h-11 items-center gap-3 px-2 ${collapsed ? 'justify-center' : ''}`}>
            <span className="relative grid size-10 shrink-0 place-items-center rounded-xl border border-rose-300/25 bg-rose-300/[.08] text-rose-200 shadow-[0_0_26px_rgba(225,29,50,.12)]">
              <BookOpenCheck size={19} />
              <span aria-hidden="true" className="absolute -right-1 -top-1 size-2 rounded-full bg-rose-400" />
            </span>
            {!collapsed && <span className="min-w-0"><span className="block truncate text-[13px] font-semibold tracking-wide text-white">Academic AI</span><span className="mt-0.5 block text-[9px] font-medium tracking-[.22em] text-slate-500">FOR IMPACT</span></span>}
          </Link>

          {!collapsed && <div className="mb-3 px-3 text-[9px] font-semibold uppercase tracking-[.2em] text-slate-600">{role === 'TEACHER' ? 'Faculty workspace' : 'Learning space'}</div>}
          {navigation()}

          <div className="mt-auto">
            <div className="mb-3 border-t border-white/[.07] pt-4">{!collapsed && <div className="mb-3 px-3 text-[9px] font-semibold uppercase tracking-[.2em] text-slate-600">Account</div>}
              <button type="button" onClick={() => setProfileOpen(!profileOpen)} aria-expanded={profileOpen} className={`flex min-h-12 w-full items-center gap-3 rounded-lg border border-white/[.07] bg-white/[.025] px-2.5 text-left hover:bg-white/[.05] ${collapsed ? 'justify-center px-0' : ''}`}>
                <span className="grid size-8 shrink-0 place-items-center rounded-full border border-white/10 bg-white/[.06] text-xs font-semibold text-slate-100">{profile?.name?.split(' ').map((part) => part[0]).slice(0, 2).join('') ?? 'AI'}</span>
                {!collapsed && <><span className="min-w-0 flex-1"><span className="block truncate text-xs font-medium text-slate-200">{profile?.name ?? 'Loading profile'}</span><span className="mt-0.5 block text-[10px] text-slate-500">{role === 'TEACHER' ? 'Faculty account' : 'Student account'}</span></span><ChevronDown size={14} className={`shrink-0 text-slate-500 transition-transform ${profileOpen ? 'rotate-180' : ''}`} /></>}
              </button>
              <AnimatePresence>
                {profileOpen && <motion.div initial={reduceMotion ? false : { opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 4 }} transition={{ duration: reduceMotion ? 0 : .16, ease: 'easeOut' }} className="mt-2 rounded-lg border border-white/10 bg-[#1b1d21] p-1.5">
                  <div className="truncate px-2 py-2 text-[10px] text-slate-500">{profile?.email ?? 'Signed-in account'}</div>
                  <button type="button" onClick={logout} className="flex min-h-9 w-full items-center gap-2 rounded-md px-2 text-xs text-slate-300 transition hover:bg-rose-500/10 hover:text-rose-200"><LogOut size={14} /> Sign out</button>
                </motion.div>}
              </AnimatePresence>

            </div>
            <button type="button" onClick={() => setCollapsed(!collapsed)} aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} aria-expanded={!collapsed} className="flex min-h-9 w-full items-center justify-center gap-2 rounded-lg text-xs text-slate-500 hover:bg-white/[.04] hover:text-slate-200">
              <ChevronLeft size={15} className={`transition-transform ${collapsed ? 'rotate-180' : ''}`} />{!collapsed && <span>Collapse sidebar</span>}
            </button>
          </div>
        </aside>

        <div className="min-w-0 flex-1">
          <header className="sticky top-0 z-40 border-b border-white/[.07] bg-[#111214]/90 backdrop-blur-xl">
            <div className="flex min-h-[68px] items-center gap-3 px-4 sm:px-6 xl:px-9">
              <button ref={menuTriggerRef} type="button" onClick={() => setMenuOpen(true)} aria-label="Open navigation" aria-expanded={menuOpen} className="grid size-10 shrink-0 place-items-center rounded-lg border border-white/10 text-slate-300 hover:bg-white/[.05] lg:hidden"><Menu size={18} /></button>
              <Link href={role === 'TEACHER' ? '/teacher/dashboard' : '/student/dashboard'} className="flex items-center gap-2.5 lg:hidden">
                <span className="grid size-8 place-items-center rounded-lg border border-rose-300/20 bg-rose-300/[.08] text-rose-200"><BookOpenCheck size={16} /></span>
                <span className="text-xs font-semibold text-white">Academic AI</span>
              </Link>
              <div className="hidden min-w-0 lg:block"><div className="text-[9px] font-medium uppercase tracking-[.18em] text-slate-600">{role === 'TEACHER' ? 'Faculty workspace' : 'Student workspace'}</div><div className="mt-1 truncate text-sm font-medium text-slate-100">{currentTitle}</div></div>

              <div className="relative ml-auto w-full min-w-0 max-w-[360px]">
                <label htmlFor="workspace-search" className="sr-only">Search workspace pages</label>
                <Search size={15} aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                <input id="workspace-search" value={searchTerm} onFocus={() => setSearchOpen(true)} onChange={(event) => { setSearchTerm(event.target.value); setSearchOpen(true); }} onKeyDown={(event) => { if (event.key === 'Enter' && searchResults[0]) { event.preventDefault(); openRoute(searchResults[0].href); } }} placeholder="Find a page…" aria-controls="workspace-search-results" aria-expanded={searchOpen} role="combobox" aria-autocomplete="list" className="h-10 w-full rounded-lg border border-white/[.09] bg-white/[.025] pl-9 pr-14 text-xs text-slate-200 outline-none transition placeholder:text-slate-600 focus:border-rose-400/40 focus:bg-white/[.04]" />
                <kbd className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 rounded border border-white/10 px-1.5 py-1 text-[9px] text-slate-600">⌘ K</kbd>
                <AnimatePresence>
                  {searchOpen && <motion.div id="workspace-search-results" role="listbox" initial={reduceMotion ? false : { opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 5 }} transition={{ duration: reduceMotion ? 0 : .16, ease: 'easeOut' }} className="surface absolute left-0 right-0 top-12 z-50 overflow-hidden rounded-xl p-1.5 shadow-2xl">
                    {searchResults.length ? searchResults.map(({ label, href, icon: Icon }) => <button key={href} type="button" role="option" aria-selected={pathname === href} onMouseEnter={() => router.prefetch(href)} onFocus={() => router.prefetch(href)} onMouseDown={(event) => event.preventDefault()} onClick={() => openRoute(href)} className="flex min-h-10 w-full items-center gap-3 rounded-lg px-3 text-left text-xs text-slate-300 hover:bg-white/[.06] hover:text-white"><Icon size={15} className="text-rose-300" />{label}<span className="ml-auto text-[9px] text-slate-600">Open</span></button>) : <p className="px-3 py-4 text-xs text-slate-500">No matching workspace pages.</p>}
                  </motion.div>}
                </AnimatePresence>

                <Link
                  href={role === 'TEACHER' ? '/teacher/early-warnings' : '/student/support'}
                  aria-label={role === 'TEACHER' ? 'Review signals' : 'Find resources'}
                  title={role === 'TEACHER' ? 'Review signals' : 'Find resources'}
                  className="quick-action fixed bottom-4 right-4 z-30 inline-flex min-h-12 items-center gap-2 rounded-full border border-white/15 bg-rose-600 px-4 text-xs font-semibold text-white shadow-[0_12px_36px_rgba(0,0,0,.4),0_0_24px_rgba(225,29,50,.2)] transition hover:-translate-y-0.5 hover:bg-rose-500 focus-visible:ring-2 focus-visible:ring-rose-300 sm:bottom-6 sm:right-6"
                >
                  <LifeBuoy size={16} aria-hidden="true" />
                  <span className="hidden sm:inline">{role === 'TEACHER' ? 'Review signals' : 'Find resources'}</span>
                </Link>
              </div>

              <div className="relative shrink-0">
                <button type="button" onClick={() => setNoticeOpen(!noticeOpen)} aria-label={`Notifications${notices.some((item) => !item.read) ? ', unread items' : ''}`} aria-expanded={noticeOpen} className="relative grid size-10 place-items-center rounded-lg text-slate-300 transition hover:bg-white/[.05]">
                  <Bell size={17} />
                  {notices.some((item) => !item.read) && <span aria-label="Unread notifications" className="red-pulse absolute right-2.5 top-2.5 size-1.5 rounded-full bg-rose-400" />}
                </button>
                <AnimatePresence>
                  {noticeOpen && <motion.div initial={reduceMotion ? false : { opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 5 }} transition={{ duration: reduceMotion ? 0 : .16, ease: 'easeOut' }} className="surface absolute right-0 top-12 z-50 w-[min(22rem,calc(100vw-2rem))] rounded-xl p-3 shadow-2xl">
                    <div className="mb-2 flex items-center justify-between"><span className="text-xs font-semibold text-white">Notifications</span><button type="button" onClick={() => setNoticeOpen(false)} aria-label="Close notifications" className="grid size-8 place-items-center rounded-md text-slate-400 hover:bg-white/[.06]"><X size={15} /></button></div>
                    <div className="max-h-[min(60vh,24rem)] overflow-auto">{notices.length ? notices.slice(0, 8).map((notice) => <div key={notice.id} className="flex gap-2 border-t border-white/[.07] py-3"><span className={`mt-1 size-1.5 shrink-0 rounded-full ${notice.read ? 'bg-slate-700' : 'bg-rose-400'}`} /><div><div className="text-xs font-medium text-slate-200">{notice.title}</div><p className="mt-1 text-[11px] leading-5 text-slate-500">{notice.body}</p></div></div>) : <p className="py-4 text-xs text-slate-500">You are all caught up.</p>}</div>
                  </motion.div>}
                </AnimatePresence>
              </div>
            </div>
          </header>

          <main className="mx-auto min-h-[calc(100vh-68px)] max-w-[1600px] px-4 pb-16 pt-7 sm:px-6 xl:px-9">
            {error ? <div role="alert" className="text-sm text-rose-200">{error}</div> : profile ? <motion.div key={pathname} initial={reduceMotion ? false : { opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: reduceMotion ? 0 : .22, ease: 'easeOut' }} className="page-content">{children}</motion.div> : <div aria-label="Loading workspace" className="space-y-4"><div className="skeleton h-9 w-56 rounded-md" /><div className="skeleton h-28 rounded-xl" /></div>}
          </main>
        </div>
      </div>

      <AnimatePresence>
        {menuOpen && <motion.div className="fixed inset-0 z-[60] lg:hidden" initial={reduceMotion ? false : { opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: reduceMotion ? 0 : .18 }}>
          <button type="button" aria-label="Close navigation" onClick={() => setMenuOpen(false)} className="absolute inset-0 h-full w-full cursor-default bg-black/65 backdrop-blur-[2px]" />
          <motion.aside ref={mobileNavRef} id="mobile-primary-navigation" role="dialog" aria-modal="true" aria-label="Mobile primary navigation" onKeyDown={containMobileMenuFocus} initial={reduceMotion ? false : { x: -290 }} animate={{ x: 0 }} exit={{ x: reduceMotion ? 0 : -290 }} transition={{ duration: reduceMotion ? 0 : .22, ease: 'easeOut' }} className="relative flex h-full w-[min(290px,86vw)] flex-col border-r border-white/10 bg-[#111214] p-4 shadow-2xl">
            <div className="mb-8 flex items-center justify-between"><Link href={role === 'TEACHER' ? '/teacher/dashboard' : '/student/dashboard'} onClick={() => setMenuOpen(false)} className="flex items-center gap-3"><span className="grid size-9 place-items-center rounded-lg border border-rose-300/20 bg-rose-300/[.08] text-rose-200"><BookOpenCheck size={18} /></span><span><span className="block text-xs font-semibold">Academic AI</span><span className="text-[9px] tracking-[.18em] text-slate-500">FOR IMPACT</span></span></Link><button ref={closeMobileMenuRef} type="button" onClick={() => setMenuOpen(false)} aria-label="Close navigation" className="grid size-9 place-items-center rounded-lg text-slate-400 hover:bg-white/[.05]"><X size={18} /></button></div>
            <div className="mb-3 px-3 text-[9px] font-semibold uppercase tracking-[.2em] text-slate-600">{role === 'TEACHER' ? 'Faculty workspace' : 'Learning space'}</div>
            <div className="flex-1">{navigation(true)}</div>
            <button type="button" onClick={logout} className="flex min-h-10 items-center gap-3 rounded-lg px-3 text-xs text-slate-400 hover:bg-rose-500/10 hover:text-rose-200"><LogOut size={16} /> Sign out</button>
          </motion.aside>
        </motion.div>}
      </AnimatePresence>
    </div>
  );
}
