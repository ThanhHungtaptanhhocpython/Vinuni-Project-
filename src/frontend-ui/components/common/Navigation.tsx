import Link from "next/link";

function Logo() {
  return (
    <svg width={18} height={18} viewBox="0 0 256 256" fill="none" aria-hidden="true">
      <path
        d="M 160 88 L 194 34 L 216 0 L 256 0 L 256 40 L 221.5 93.5 L 200 128 L 256 128 L 256 256 L 96 256 L 96 168 L 64.246 220 L 40 256 L 0 256 L 0 216 L 34 162 L 56 128 L 0 128 L 0 0 L 160 0 Z"
        fill="var(--paper-50)"
      />
    </svg>
  );
}

const NAV_LINKS = [
  { href: "/", label: "Trang chủ" },
  { href: "/product", label: "Sản phẩm" },
  { href: "/support", label: "Hỗ trợ" },
  { href: "/contact", label: "Liên hệ" },
];

interface NavigationProps {
  activeHref?: string;
}

export function Navigation({ activeHref }: NavigationProps) {
  return (
    <nav className="grid grid-cols-[1fr_auto_1fr] items-center gap-3 pt-4 sm:pt-6 px-4 sm:px-8 py-4">
      <Link
        href="/"
        aria-label="VAGENT — về đầu trang"
        className="liquid-glass liquid-glass--on-bright flex items-center justify-center rounded-full w-10 h-10 sm:w-11 sm:h-11 shrink-0 focus-ring"
      >
        <Logo />
      </Link>

      <div className="flex justify-center min-w-0">
        <div className="liquid-glass liquid-glass--on-bright flex items-center gap-4 sm:gap-8 rounded-full px-5 sm:px-7 py-2.5 sm:py-3">
          {NAV_LINKS.map((link) => (
            <a
              key={link.href}
              href={link.href}
              className={`text-[13px] sm:text-[15px] font-medium transition-colors duration-200 focus-ring rounded whitespace-nowrap ${
                activeHref === link.href
                  ? "text-paper-50"
                  : "text-paper-50/85 hover:text-paper-50"
              }`}
            >
              {link.label}
            </a>
          ))}
        </div>
      </div>

      <div className="flex items-center justify-end gap-2">
        <Link
          href="/register"
          className="liquid-glass liquid-glass--on-bright inline-flex items-center rounded-full px-4 sm:px-5 h-9 text-[14px] font-medium text-paper-50 hover:bg-arc-500 transition-all duration-200 focus-ring whitespace-nowrap"
        >
          Đăng ký
        </Link>
        <Link
          href="/login"
          className="liquid-glass liquid-glass--on-bright inline-flex items-center rounded-full px-4 sm:px-5 h-9 text-[14px] font-medium text-paper-50 hover:bg-arc-500 transition-all duration-200 focus-ring whitespace-nowrap"
        >
          Đăng nhập
        </Link>
      </div>
    </nav>
  );
}
