import Link from "next/link";
import { Navigation } from "@/components/common/Navigation";
import { Search } from "lucide-react";

const CATEGORIES = [
  {
    title: "Bắt đầu",
    items: ["Tổng quan hệ thống", "Cài đặt", "Chạy lần đầu", "Cấu hình"],
  },
  {
    title: "Agent",
    items: ["LangGraph", "Lập kế hoạch", "Tool Calling", "Memory"],
  },
  {
    title: "VLA",
    items: ["SmolVLA", "Policy", "Inference", "Action Space"],
  },
  {
    title: "Edge",
    items: ["MCP", "Controller", "Safety", "Streaming"],
  },
  {
    title: "Robot",
    items: ["Simulation", "SO-101", "LeRobot", "Camera"],
  },
  {
    title: "Triển khai",
    items: ["Local dev", "Cloud", "Edge", "Production"],
  },
];

const FAQ_ITEMS = [
  {
    question: "Agent cho VLA Robot là gì?",
    answer:
      "Hệ thống cho phép điều khiển robot bằng ngôn ngữ tự nhiên. Agent sử dụng LLM để suy luận và lập kế hoạch, sau đó điều phối các tool để thực thi tác vụ trên robot thông qua VLA policy.",
  },
  {
    question: "Cloud Agent làm gì?",
    answer:
      "Cloud Agent (FastAPI + LangGraph) xử lý logic nghiệp vụ: xác thực, quản lý session, giao tiếp với LLM, và điều phối các tool thông qua MCP.",
  },
  {
    question: "Edge VLA hoạt động như thế nào?",
    answer:
      "MCP server chạy trên thiết bị edge (Jetson/PC), giao tiếp trực tiếp với robot qua USB Serial. Nó thực thi VLA policy và đảm bảo an toàn.",
  },
  {
    question: "Tôi có thể dùng simulation không?",
    answer:
      "Có. Hệ thống hỗ trợ chạy trên LIBERO/MuJoCo để phát triển và test policy trước khi triển khai lên robot thật.",
  },
];

export default function SupportPage() {
  return (
    <div className="min-h-screen" style={{ backgroundColor: "#FBFAFA" }}>
      <Navigation activeHref="/support" />

      {/* Hero */}
      <section className="px-6 sm:px-12 md:px-20 lg:px-28 py-16 sm:py-20 lg:py-24 border-b" style={{ borderColor: "#E5E5E5" }}>
        <div className="max-w-2xl">
          <p className="eyebrow mb-4">Hỗ trợ</p>
          <h1 className="display-xl text-[var(--ink-950)] mb-4">Hỗ trợ hệ thống.</h1>
          <p className="body-text text-[var(--ink-950)]/70 mb-8">
            Tìm tài liệu, hướng dẫn triển khai và giải pháp cho các vấn đề
            thường gặp trong hệ thống Agent–VLA.
          </p>

          {/* Search */}
          <div className="relative max-w-lg">
            <Search size={18} className="absolute left-4 top-1/2 -translate-y-1/2 text-haze-500" />
            <input
              type="text"
              placeholder="Search documentation..."
              className="w-full h-12 pl-12 pr-4 rounded-lg border text-[15px] text-paper-50 placeholder:text-haze-500 focus-ring bg-transparent"
              style={{
                borderColor: "#1F2937",
              }}
            />
          </div>
        </div>
      </section>

      {/* Documentation Categories */}
      <section className="px-6 sm:px-12 md:px-20 lg:px-28 py-12 border-b" style={{ borderColor: "#E5E5E5" }}>
        <h2 className="display-l text-paper-50 mb-8">Tài liệu</h2>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 max-w-5xl">
          {CATEGORIES.map((category) => (
            <div key={category.title} className="panel p-5">
              <h3 className="title text-paper-50 mb-4">{category.title}</h3>
              <ul className="space-y-2">
                {category.items.map((item) => (
                  <li key={item}>
                    <a href="#" className="text-[14px] text-paper-50/70 hover:text-paper-50 transition-colors">
                      {item}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </section>

      {/* FAQ */}
      <section className="px-6 sm:px-12 md:px-20 lg:px-28 py-16 border-b" style={{ borderColor: "#E5E5E5" }}>
        <h2 className="display-l text-[var(--ink-950)] mb-8">Câu hỏi thường gặp</h2>

        <div className="max-w-3xl space-y-4">
          {FAQ_ITEMS.map((faq) => (
            <div key={faq.question} className="panel p-6">
              <h3 className="title text-paper-50 mb-3">{faq.question}</h3>
              <p className="body-text text-paper-50/70">{faq.answer}</p>
            </div>
          ))}
        </div>
      </section>

      {/* CTA */}
      <section className="px-6 sm:px-12 md:px-20 lg:px-28 py-16">
        <div className="max-w-2xl">
          <h2 className="display-l text-[var(--ink-950)] mb-4">Cần thêm hỗ trợ?</h2>
          <p className="body-text text-[var(--ink-950)]/70 mb-8">
            Nếu bạn có câu hỏi hoặc cần hỗ trợ thêm, đừng ngần ngại liên hệ
            với chúng tôi.
          </p>
          <Link href="/contact" className="btn-primary focus-ring">
            Liên hệ hỗ trợ
          </Link>
        </div>
      </section>
    </div>
  );
}
