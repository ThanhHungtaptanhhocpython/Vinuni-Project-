/* P00 — Landing / Marketing page
   MASTER §8: THE ONLY page with a background video.
   No LIVE badge, no FPS, no HUD overlays.
   §9.1: liquid-glass whitelist — nav pills, badge, CTA. */

import Link from "next/link";
import Image from "next/image";
import { HeroVideo } from "@/components/marketing/HeroVideo";
import { ScrollReveal } from "@/components/marketing/ScrollReveal";
import { ArrowRight, Server, Box, Cpu, Shield, Clock, Activity, Command, Network, User, Cloud, Mail, Phone, MapPin } from "lucide-react";

// Logo SVG component — inline per P00 spec
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
  { href: "#vagent", label: "Vagent" },
  { href: "#architecture", label: "Kiến trúc" },
  { href: "#capabilities", label: "Năng lực" },
  { href: "#about", label: "Giới thiệu" },
];

export default function LandingPage() {
  return (
    <div data-theme="dark" className="min-h-screen bg-ink-950 text-paper-50 font-sans">
      <noscript>
        <style>{`
          .reveal-slide-up, .reveal-fade-in {
            opacity: 1 !important;
            transform: none !important;
          }
        `}</style>
      </noscript>
      {/* 1. HERO SECTION */}
      <section id="vagent" className="relative min-h-screen overflow-hidden">
        <HeroVideo />

        <div className="relative z-10 flex flex-col min-h-screen">
          <nav className="flex flex-wrap lg:grid lg:grid-cols-[1fr_auto_1fr] items-center justify-between gap-3 pt-4 sm:pt-6 px-4 sm:px-8">
            <Link
              href="/"
              aria-label="VAGENT — trang chủ"
              className="hero-glass-pill flex items-center justify-center rounded-full overflow-hidden isolate w-10 h-10 sm:w-11 sm:h-11 shrink-0 focus-ring"
            >
              <Logo />
            </Link>

            <div className="flex justify-start lg:justify-center w-full lg:w-auto order-3 lg:order-none overflow-x-auto lg:overflow-visible pb-2 lg:pb-0" style={{ scrollbarWidth: "none" }}>
              <div className="hero-nav-pill flex items-center gap-4 sm:gap-8 rounded-full overflow-hidden isolate px-5 sm:px-7 py-2.5 sm:py-3 shrink-0">
                {NAV_LINKS.map((link) => (
                  <a
                    key={link.href}
                    href={link.href}
                    className="text-[13px] sm:text-[15px] font-medium text-paper-50/85 hover:text-paper-50 transition-colors duration-200 focus-ring rounded whitespace-nowrap"
                  >
                    {link.label}
                  </a>
                ))}
              </div>
            </div>

            <div className="flex items-center justify-end gap-2 order-2 lg:order-none">
              <Link
                href="/login"
                className="hero-glass-pill inline-flex items-center rounded-full overflow-hidden isolate px-4 sm:px-5 h-9 text-[14px] font-medium text-paper-50 hover:bg-arc-500 transition-all duration-200 focus-ring whitespace-nowrap"
              >
                Khởi chạy Vagent
              </Link>
            </div>
          </nav>

          <div className="flex-1 flex items-end pb-10 sm:pb-16 lg:pb-20 px-6 sm:px-12 md:px-20 lg:px-28">
            <div className="max-w-xl">
              <h1 className="display-xl text-[1.75rem] sm:text-[2.25rem] lg:text-[2.75rem] text-[var(--ink-950)] mb-3">
                Trí tuệ trên Cloud.{" "}
                <span className="block">Hành động tại Edge.</span>
              </h1>

              <p className="text-[14px] sm:text-[15px] text-[var(--ink-950)]/75 font-normal max-w-md mb-6">
                Vagent là lớp điều phối Cloud-Edge kết nối các agent, mô hình VLA và robot.
              </p>

              <div className="flex flex-wrap items-center gap-3">
                <Link
                  href="/login"
                  className="group inline-flex items-center gap-2 text-[14px] font-medium text-paper-50 hero-glass-pill rounded-full overflow-hidden isolate px-5 py-2.5 hover:bg-arc-500 transition-all duration-200 focus-ring"
                >
                  Khởi chạy Vagent
                  <ArrowRight size={16} className="transition-transform duration-200 group-hover:translate-x-0.5" aria-hidden="true" />
                </Link>
                <a
                  href="#architecture"
                  className="group inline-flex items-center gap-2 text-[14px] font-medium text-[var(--ink-950)]/80 hover:text-[var(--ink-950)] transition-colors duration-200 px-5 py-2.5 focus-ring rounded-full"
                >
                  Khám phá kiến trúc
                </a>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* 2. PROBLEM DIAGRAM / CONTEXT */}
      <section className="border-t border-[var(--line)] px-6 sm:px-12 md:px-20 lg:px-28 py-16 sm:py-24 bg-ink-900">
        <ScrollReveal>
          <p className="eyebrow mb-3 text-cyan-400">Khoảng trống kết nối</p>
          <h2 className="display-l max-w-2xl mb-4 text-paper-50">
            Xóa bỏ khoảng cách giữa suy luận bậc cao và thực thi vật lý.
          </h2>
          <p className="body-text text-haze-300 max-w-2xl mb-12">
            Việc kết nối các agent trên cloud với các robot triển khai tại edge đòi hỏi một kiến trúc phân tán cân bằng giữa năng lực và khả năng kiểm soát.
          </p>
        </ScrollReveal>

        <div className="flex flex-col md:flex-row gap-6 md:gap-12 items-center opacity-80">
          <ScrollReveal animation="fade-in" delay={100} className="panel p-6 flex-1 flex flex-col items-center text-center border-halt-500/20 w-full md:w-auto">
            <Server className="w-8 h-8 text-haze-500 mb-4" />
            <h3 className="title text-[16px] mb-2 text-paper-50">Năng lực Cloud</h3>
            <p className="text-[14px] text-haze-300">Vận hành các mô hình suy luận và agent ở nơi có tài nguyên tính toán dồi dào.</p>
          </ScrollReveal>
          <div className="hidden md:block w-px h-16 bg-line-strong"></div>
          <ScrollReveal animation="fade-in" delay={200} className="panel p-6 flex-1 flex flex-col items-center text-center border-halt-500/20 w-full md:w-auto">
            <Cpu className="w-8 h-8 text-haze-500 mb-4" />
            <h3 className="title text-[16px] mb-2 text-paper-50">Thực thi tại Edge</h3>
            <p className="text-[14px] text-haze-300">Chuyển đổi lệnh thành điều khiển động cơ ở vị trí gần với phần cứng hơn.</p>
          </ScrollReveal>
        </div>
      </section>

      {/* 3. ARCHITECTURE SECTION */}
      <section id="architecture" className="border-t border-[var(--line)] px-6 sm:px-12 md:px-20 lg:px-28 py-16 sm:py-24 bg-ink-950">
        <ScrollReveal>
          <p className="eyebrow mb-3 text-jade-400">Kiến trúc</p>
          <h2 className="display-l max-w-3xl mb-12 text-paper-50">
            Một luồng xử lý liền mạch từ ý định đến chuyển động.
          </h2>
        </ScrollReveal>

        <div className="relative z-0 flex flex-col md:flex-row justify-between items-center md:items-stretch gap-4 md:gap-2">
          {/* Path Line connecting blocks */}
          <div className="hidden md:block absolute top-1/2 left-0 right-0 h-[24px] -z-10 -translate-y-1/2 overflow-hidden w-full">
            <ScrollReveal animation="fade-in" delay={300} className="w-full h-full relative">
              <div className="absolute top-1/2 left-0 right-0 h-[2px] bg-line-strong -translate-y-1/2"></div>
              <div className="absolute top-1/2 h-[4px] w-28 bg-cyan-400 rounded-full shadow-[0_0_12px_var(--cyan-400)] -translate-y-1/2 animate-flow-horizontal"></div>
            </ScrollReveal>
          </div>

          {[
            { label: "Người dùng", sub: "Ngôn ngữ tự nhiên", icon: User },
            { label: "Cloud Agent", sub: "Lập kế hoạch tác vụ", icon: Cloud },
            { label: "MCP", sub: "Cầu nối ngữ cảnh", icon: Network },
            { label: "Edge Runtime", sub: "Thực thi", icon: Command },
            { label: "VLA", sub: "Thị giác-Ngôn ngữ", icon: Box },
            { label: "SO-101", sub: "Truyền động robot", icon: Activity },
          ].map((step, idx, arr) => (
            <ScrollReveal key={idx} animation="fade-in" delay={idx * 100} className="flex flex-col items-center w-full md:w-auto relative group">
              <div className="panel-inset px-4 py-4 w-full md:w-32 flex flex-col items-center justify-center text-center gap-2 mb-2 md:mb-0 h-full">
                <step.icon className="w-5 h-5 text-arc-400" />
                <span className="ui-text text-[13px] text-paper-50 leading-tight">{step.label}</span>
                <span className="text-[11px] text-haze-500 uppercase tracking-wider">{step.sub}</span>
              </div>
              {idx < arr.length - 1 && (
                <div className="md:hidden w-px h-6 bg-line relative my-1 overflow-hidden">
                   <div className="absolute inset-0 bg-gradient-to-b from-transparent via-cyan-400/80 to-transparent animate-flow-vertical" />
                </div>
              )}
            </ScrollReveal>
          ))}
        </div>
      </section>

      {/* 4. INTENT-TO-ACTION WORKFLOW */}
      <section className="border-t border-[var(--line)] px-6 sm:px-12 md:px-20 lg:px-28 py-16 sm:py-24 bg-ink-900">
        <ScrollReveal>
          <p className="eyebrow mb-3">Quy trình làm việc</p>
          <h2 className="display-l max-w-2xl mb-10 text-paper-50">Từ ngôn từ đến hành động vật lý.</h2>
        </ScrollReveal>

        <div className="flex flex-col md:flex-row gap-4">
          {[
            { step: "01", title: "Ý định", desc: "Người dùng thể hiện yêu cầu bằng ngôn ngữ tự nhiên." },
            { step: "02", title: "Lập kế hoạch", desc: "Cloud Agent phân tích tác vụ thành các bước logic." },
            { step: "03", title: "Nhận thức", desc: "Mô hình VLA xử lý luồng video trực tiếp từ camera." },
            { step: "04", title: "Hành động", desc: "SO-101 thực thi quỹ đạo không gian khớp chính xác." },
          ].map((item, idx) => (
            <ScrollReveal key={item.step} animation="fade-in" delay={idx * 100} className="panel p-5 flex-1 flex flex-col hover:border-[var(--line-strong)] transition-colors w-full">
              <span className="text-[12px] font-mono text-haze-500 mb-4">{item.step}</span>
              <h3 className="title text-[16px] mb-2">{item.title}</h3>
              <p className="text-[14px] text-haze-300">{item.desc}</p>
            </ScrollReveal>
          ))}
        </div>
      </section>

      {/* 5. 4 GROUNDED CAPABILITIES */}
      <section id="capabilities" className="border-t border-[var(--line)] px-6 sm:px-12 md:px-20 lg:px-28 py-16 sm:py-24 bg-ink-950">
        <ScrollReveal>
          <p className="eyebrow mb-3">Năng lực</p>
          <h2 className="display-l max-w-2xl mb-10 text-paper-50">Gắn liền với thực tại vật lý.</h2>
        </ScrollReveal>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <ScrollReveal animation="fade-in" delay={100} className="panel p-6 lg:p-8">
            <Box className="w-6 h-6 text-arc-400 mb-4" />
            <h3 className="title text-[18px] mb-2">Gắn kết Thị giác-Ngôn ngữ</h3>
            <p className="text-[14px] text-haze-300 leading-[1.6]">
              Các agent không hoạt động một cách mù quáng. Chúng quan sát hiện trường thông qua luồng camera trực tiếp, xác minh các điều kiện tiền đề trước khi thực thi.
            </p>
          </ScrollReveal>
          <ScrollReveal animation="fade-in" delay={200} className="panel p-6 lg:p-8">
            <Activity className="w-6 h-6 text-cyan-400 mb-4" />
            <h3 className="title text-[18px] mb-2">Phản hồi trạng thái robot</h3>
            <p className="text-[14px] text-haze-300 leading-[1.6]">
              Xem các cập nhật định kỳ từ robot bao gồm vị trí các khớp và trạng thái để theo dõi tình trạng của máy móc.
            </p>
          </ScrollReveal>
          <ScrollReveal animation="fade-in" delay={100} className="panel p-6 lg:p-8">
            <Shield className="w-6 h-6 text-jade-400 mb-4" />
            <h3 className="title text-[18px] mb-2">Kiểm soát dừng an toàn</h3>
            <p className="text-[14px] text-haze-300 leading-[1.6]">
              Nút DỪNG (STOP) trên giao diện phần mềm luôn hiển thị khi robot đang hoạt động. Một thao tác duy nhất sẽ gửi lệnh yêu cầu dừng quá trình thực thi.
            </p>
          </ScrollReveal>
          <ScrollReveal animation="fade-in" delay={200} className="panel p-6 lg:p-8">
            <Clock className="w-6 h-6 text-amber-400 mb-4" />
            <h3 className="title text-[18px] mb-2">Tự chủ theo lịch trình</h3>
            <p className="text-[14px] text-haze-300 leading-[1.6]">
              Thiết lập agent để chạy các tác vụ định kỳ. Agent xử lý thời gian, đánh thức Edge Runtime, thực thi và lưu lại bằng chứng hình ảnh về việc hoàn thành.
            </p>
          </ScrollReveal>
        </div>
      </section>

      {/* 6. VISUAL-FIRST SO-101 SHOWCASE */}
      <section className="border-t border-[var(--line)] px-6 sm:px-12 md:px-20 lg:px-28 py-16 sm:py-24 bg-ink-900">
        <div className="max-w-4xl mx-auto">
          <ScrollReveal className="mb-10 text-center">
            <p className="eyebrow mb-3">Phần cứng</p>
            <h2 className="display-l mb-4 text-paper-50">Tích hợp robot SO-101</h2>
          </ScrollReveal>

          <ScrollReveal animation="fade-in" delay={150} className="panel overflow-hidden relative border-[var(--line-strong)] p-1">
            <div className="aspect-video relative rounded-sm overflow-hidden bg-ink-950 flex flex-col justify-end">
              <Image src="/media/so-arm101-details-1.jpg" alt="SO-101 hardware showcase" fill className="object-cover object-center" />
            </div>
          </ScrollReveal>
        </div>
      </section>

      {/* 7. ABOUT & FINAL CTA / FOOTER */}
      <section id="about" className="border-t border-[var(--line)] px-6 sm:px-12 md:px-20 lg:px-28 py-20 sm:py-32 bg-ink-950">
        <div className="max-w-4xl mx-auto flex flex-col items-center text-center">
          <ScrollReveal>
            <p className="eyebrow mb-3 text-arc-400">Dự án P-172</p>
            <h2 className="display-l mb-6 text-paper-50">
              Ranh giới điều phối kết nối suy luận trên Cloud và thực thi vật lý.
            </h2>
            <p className="body-text text-[var(--haze-300)] max-w-2xl mx-auto mb-14">
              Vagent P-172 được xây dựng làm hạ tầng nghiên cứu. Hệ thống đóng vai trò là cầu nối giữa các agent nhận thức và nền tảng robot.
            </p>
          </ScrollReveal>

          <div className="flex flex-col md:flex-row w-full gap-4 mb-16 text-left">
            <ScrollReveal animation="fade-in" delay={100} className="panel p-6 flex-1 border-[var(--line-strong)] w-full">
              <Cloud className="w-6 h-6 text-haze-500 mb-4" />
              <h3 className="title text-[16px] mb-2 text-paper-50">Suy luận trên Cloud</h3>
              <p className="text-[14px] text-haze-400">Điều phối các agent và mô hình lớn cho việc lập kế hoạch tác vụ bậc cao và nhận thức.</p>
            </ScrollReveal>
            <ScrollReveal animation="fade-in" delay={200} className="panel p-6 flex-1 border-[var(--line-strong)] w-full">
              <Command className="w-6 h-6 text-haze-500 mb-4" />
              <h3 className="title text-[16px] mb-2 text-paper-50">Thực thi tại Edge</h3>
              <p className="text-[14px] text-haze-400">Chuyển đổi ý định ngữ nghĩa thành các lệnh phần cứng chính xác tại biên mạng cục bộ.</p>
            </ScrollReveal>
            <ScrollReveal animation="fade-in" delay={300} className="panel p-6 flex-1 border-[var(--line-strong)] w-full">
              <Activity className="w-6 h-6 text-haze-500 mb-4" />
              <h3 className="title text-[16px] mb-2 text-paper-50">Phản hồi quan sát được</h3>
              <p className="text-[14px] text-haze-400">Khép kín vòng lặp thực thi với gắn kết thị giác-ngôn ngữ và phản hồi trạng thái robot.</p>
            </ScrollReveal>
          </div>

          <ScrollReveal delay={400}>
            <Link
              href="/login"
              className="btn-primary focus-ring px-8 h-11 text-[15px]"
            >
              Khởi chạy Vagent
            </Link>
          </ScrollReveal>
        </div>

        {/* MOCK CONTACT STRIP */}
        <ScrollReveal delay={500} className="mt-28 w-full">
          <div className="panel flex flex-col lg:flex-row justify-between gap-10 p-8 sm:p-12 border-[var(--line-strong)] bg-ink-900/40 text-left rounded-2xl">
            <div className="flex flex-col gap-4 max-w-md">
              <h3 className="text-paper-50 font-medium text-[20px] flex items-center gap-3">
                <Logo /> Vagent
              </h3>
              <p className="text-[15px] text-haze-400 leading-relaxed">
                Nền tảng điều phối Cloud-Edge cho hệ sinh thái agent và robot.
              </p>
            </div>

            <div className="flex flex-col sm:flex-row gap-10 sm:gap-20">
              <div className="flex flex-col gap-4">
                <div className="flex items-center gap-3 text-[15px] text-haze-300">
                  <Mail className="w-5 h-5 text-haze-500" />
                  <span>hello@vagent.local</span>
                </div>
                <div className="flex items-center gap-3 text-[15px] text-haze-300">
                  <Phone className="w-5 h-5 text-haze-500" />
                  <span>+84 000 000 000</span>
                </div>
                <div className="flex items-start gap-3 text-[15px] text-haze-300">
                  <MapPin className="w-5 h-5 text-haze-500 shrink-0 mt-0.5" />
                  <span>P-172 Research Lab, Hanoi, Vietnam</span>
                </div>
              </div>

              <div className="flex flex-col gap-4">
                <a href="#" className="text-[15px] text-haze-300 hover:text-paper-50 transition-colors">Docs</a>
                <a href="#" className="text-[15px] text-haze-300 hover:text-paper-50 transition-colors">GitHub</a>
                <a href="#" className="text-[15px] text-haze-300 hover:text-paper-50 transition-colors">Contact</a>
              </div>
            </div>
          </div>
        </ScrollReveal>

        <footer className="mt-12 pt-8 border-t border-[var(--line)] flex flex-col md:flex-row items-center justify-between gap-4 text-[13px] text-haze-500">
          <div className="flex items-center gap-2">
            <Logo />
            <span>© {new Date().getFullYear()} Dự án Vagent.</span>
          </div>
          <div className="flex gap-4">
            <span>Hạ tầng nghiên cứu</span>
            <span aria-hidden="true" className="text-haze-500 opacity-50">•</span>
            <span>Truy cập nội bộ</span>
          </div>
        </footer>
      </section>
    </div>
  );
}
