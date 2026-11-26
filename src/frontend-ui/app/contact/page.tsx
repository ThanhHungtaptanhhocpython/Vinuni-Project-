import Link from "next/link";
import { Navigation } from "@/components/common/Navigation";

const CONTACT_INFO = {
  email: "contact@vagent.io",
  phone: "+84 28 1234 5678",
  workingHours: "Thứ 2 - Thứ 6: 9:00 - 18:00 (GMT+7)",
};

const TOPICS = [
  "Tư vấn triển khai",
  "Hỗ trợ kỹ thuật",
  "Báo lỗi",
  "Hợp tác",
  "Khác",
];

export default function ContactPage() {
  return (
    <div className="min-h-screen" style={{ backgroundColor: "#FBFAFA" }}>
      <Navigation activeHref="/contact" />

      {/* Hero */}
      <section className="px-6 sm:px-12 md:px-20 lg:px-28 py-16 sm:py-20 lg:py-24 border-b" style={{ borderColor: "#E5E5E5" }}>
        <div className="max-w-2xl">
          <p className="eyebrow mb-4">Liên hệ</p>
          <h1 className="display-xl text-[var(--ink-950)] mb-4">
            Cùng xây dựng thế hệ robot thông minh tiếp theo.
          </h1>
          <p className="body-text text-[var(--ink-950)]/70">
            Kết nối với đội ngũ để trao đổi về robotics AI, VLA, nghiên cứu
            hoặc các cơ hội hợp tác.
          </p>
        </div>
      </section>

      {/* Contact Form & Info */}
      <section className="px-6 sm:px-12 md:px-20 lg:px-28 py-12 border-b" style={{ borderColor: "#E5E5E5" }}>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-12 max-w-5xl">
          {/* Form */}
          <div>
            <h2 className="title text-[var(--ink-950)] mb-6">Gửi tin nhắn</h2>

            <form className="space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label htmlFor="name" className="block text-[14px] font-medium mb-2" style={{ color: "var(--ink-950)" }}>
                    Họ và tên
                  </label>
                  <input
                    type="text"
                    id="name"
                    required
                    className="w-full h-11 rounded-lg px-4 text-[15px] text-paper-50 placeholder:text-haze-500 focus-ring"
                    style={{
                      backgroundColor: "var(--ink-800)",
                      border: "1px solid var(--line)",
                    }}
                    placeholder="Nguyễn Văn A"
                  />
                </div>
                <div>
                  <label htmlFor="email" className="block text-[14px] font-medium mb-2" style={{ color: "var(--ink-950)" }}>
                    Email
                  </label>
                  <input
                    type="email"
                    id="email"
                    required
                    className="w-full h-11 rounded-lg px-4 text-[15px] text-paper-50 placeholder:text-haze-500 focus-ring"
                    style={{
                      backgroundColor: "var(--ink-800)",
                      border: "1px solid var(--line)",
                    }}
                    placeholder="email@example.com"
                  />
                </div>
              </div>

              <div>
                <label htmlFor="organization" className="block text-[14px] font-medium mb-2" style={{ color: "var(--ink-950)" }}>
                  Tổ chức
                </label>
                <input
                  type="text"
                  id="organization"
                  className="w-full h-11 rounded-lg px-4 text-[15px] text-paper-50 placeholder:text-haze-500 focus-ring"
                  style={{
                    backgroundColor: "var(--ink-800)",
                    border: "1px solid var(--line)",
                  }}
                  placeholder="Công ty / Trường đại học"
                />
              </div>

              <div>
                <label htmlFor="topic" className="block text-[14px] font-medium mb-2" style={{ color: "var(--ink-950)" }}>
                  Chủ đề
                </label>
                <select
                  id="topic"
                  className="w-full h-11 rounded-lg px-4 text-[15px] text-paper-50 focus-ring"
                  style={{
                    backgroundColor: "var(--ink-800)",
                    border: "1px solid var(--line)",
                  }}
                >
                  <option value="">Chọn chủ đề</option>
                  {TOPICS.map((topic) => (
                    <option key={topic} value={topic}>
                      {topic}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label htmlFor="message" className="block text-[14px] font-medium mb-2" style={{ color: "var(--ink-950)" }}>
                  Nội dung
                </label>
                <textarea
                  id="message"
                  required
                  rows={5}
                  className="w-full rounded-lg px-4 py-3 text-[15px] text-paper-50 placeholder:text-haze-500 focus-ring resize-none"
                  style={{
                    backgroundColor: "var(--ink-800)",
                    border: "1px solid var(--line)",
                  }}
                  placeholder="Mô tả chi tiết yêu cầu của bạn..."
                />
              </div>

              <button type="submit" className="btn-primary focus-ring">
                Gửi tin nhắn
              </button>
            </form>
          </div>

          {/* Contact Info */}
          <div>
            <h2 className="title text-[var(--ink-950)] mb-6">Thông tin liên hệ</h2>

            <div className="space-y-4">
              <div className="panel p-5">
                <p className="eyebrow mb-2">Email</p>
                <a href={`mailto:${CONTACT_INFO.email}`} className="title text-[17px] text-arc-400 hover:underline">
                  {CONTACT_INFO.email}
                </a>
              </div>

              <div className="panel p-5">
                <p className="eyebrow mb-2">Điện thoại</p>
                <a href={`tel:${CONTACT_INFO.phone.replace(/\s/g, "")}`} className="title text-[17px] text-arc-400 hover:underline">
                  {CONTACT_INFO.phone}
                </a>
              </div>

              <div className="panel p-5">
                <p className="eyebrow mb-2">Giờ làm việc</p>
                <p className="text-[15px] text-paper-50">{CONTACT_INFO.workingHours}</p>
              </div>
            </div>

            {/* Research & Collaboration */}
            <div className="mt-10">
              <h3 className="title text-[var(--ink-950)] mb-4">Research & Collaboration</h3>
              <p className="body-text text-[var(--ink-950)]/70 mb-4">Chúng tôi quan tâm đến:</p>
              <div className="flex flex-wrap gap-2">
                {[
                  "Agentic robotics",
                  "VLA",
                  "Robot manipulation",
                  "Sim-to-real",
                  "Intelligent control",
                ].map((topic) => (
                  <span
                    key={topic}
                    className="px-3 py-1.5 rounded-full text-[13px]"
                    style={{
                      backgroundColor: "var(--ink-800)",
                      border: "1px solid var(--line)",
                      color: "var(--haze-300)",
                    }}
                  >
                    {topic}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Bottom CTA */}
      <section className="px-6 sm:px-12 md:px-20 lg:px-28 py-16">
        <div className="flex flex-wrap gap-4">
          <Link href="/login" className="btn-primary focus-ring">
            Đăng nhập
          </Link>
          <Link href="/register" className="btn-secondary focus-ring">
            Tạo tài khoản mới
          </Link>
        </div>
      </section>
    </div>
  );
}
