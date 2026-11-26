import Link from "next/link";
import { Navigation } from "@/components/common/Navigation";

const ARCHITECTURE_LAYERS = [
  {
    name: "Giao diện người dùng",
    description: "Giao diện Next.js cho nhập tác vụ, xem camera, và giám sát trạng thái robot.",
    tech: "Next.js · TanStack Query",
  },
  {
    name: "Tầng nhận thức",
    description: "FastAPI server với LangGraph cho suy luận, lập kế hoạch, và điều phối tool.",
    tech: "FastAPI · LangGraph · LLM",
  },
  {
    name: "Tầng thực thi",
    description: "MCP server chạy VLA policies với xác minh an toàn trên thiết bị edge.",
    tech: "MCP · VLA · Safety",
  },
];

const FEATURES = [
  {
    title: "Điều khiển bằng ngôn ngữ tự nhiên",
    description:
      "Ra lệnh cho robot bằng ngôn ngữ tự nhiên. Không cần dạy toạ độ hay viết quỹ đạo.",
  },
  {
    title: "Phản hồi thị giác",
    description:
      "Nhìn thấy từng bước khi agent lập kế hoạch và thực thi. Camera và trạng thái khớp luôn hiển thị.",
  },
  {
    title: "Thực thi theo lịch",
    description:
      "Tự động hoá các tác vụ lặp lại với lịch trình. Agent chạy đúng thời gian bạn đặt.",
  },
];

export default function ProductPage() {
  return (
    <div className="min-h-screen" style={{ backgroundColor: "#FBFAFA" }}>
      <Navigation activeHref="/product" />

      {/* Hero */}
      <section className="px-6 sm:px-12 md:px-20 lg:px-28 py-20 sm:py-28 lg:py-36 border-b" style={{ borderColor: "#E5E5E5" }}>
        <div className="max-w-3xl">
          <p className="eyebrow mb-4">Sản phẩm</p>
          <h1 className="display-xl text-[var(--ink-950)] mb-6">
            Từ ngôn ngữ đến hành động của robot.
          </h1>
          <p className="body-text text-[var(--ink-950)]/70 mb-10 max-w-2xl">
            AI Agent kết hợp suy luận, lập kế hoạch và Vision-Language-Action
            để biến ý định của con người thành hành động robot.
          </p>
          <div className="flex flex-wrap gap-4">
            <Link href="/contact" className="btn-primary focus-ring">
              Liên hệ tư vấn
            </Link>
            <Link href="/login" className="btn-secondary focus-ring">
              Khám phá nền tảng
            </Link>
          </div>
        </div>
      </section>

      {/* Architecture */}
      <section className="px-6 sm:px-12 md:px-20 lg:px-28 py-16 sm:py-20 border-b" style={{ borderColor: "#E5E5E5" }}>
        <h2 className="display-l text-[var(--ink-950)] mb-4">
          Một kiến trúc được xây dựng cho robot thực.
        </h2>
        <p className="body-text text-[var(--ink-950)]/70 mb-12 max-w-2xl">
          Hệ thống gồm ba tầng chạy đồng thời: tầng giao diện, tầng nhận thức,
          và tầng thực thi trên thiết bị edge.
        </p>

        <div className="space-y-6 max-w-4xl">
          {ARCHITECTURE_LAYERS.map((layer, index) => (
            <div key={layer.name} className="panel p-6 sm:p-8">
              <div className="flex flex-col sm:flex-row sm:items-start gap-4">
                <div className="shrink-0">
                  <span className="inline-flex items-center justify-center w-8 h-8 rounded-full text-[13px] font-medium bg-arc-500 text-white">
                    {index + 1}
                  </span>
                </div>
                <div className="flex-1">
                  <h3 className="title text-[var(--paper-50)] mb-2">{layer.name}</h3>
                  <p className="body-text text-haze-300 mb-3">{layer.description}</p>
                  <p className="text-[13px] font-mono text-haze-500">{layer.tech}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Features */}
      <section className="px-6 sm:px-12 md:px-20 lg:px-28 py-16 sm:py-20 border-b" style={{ borderColor: "#E5E5E5" }}>
        <h2 className="display-l text-[var(--ink-950)] mb-12">Tính năng chính.</h2>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-8 max-w-5xl">
          {FEATURES.map((feature) => (
            <div key={feature.title} className="max-w-sm">
              <h3 className="title text-[var(--ink-950)] mb-3">{feature.title}</h3>
              <p className="body-text text-[var(--ink-950)]/70">{feature.description}</p>
            </div>
          ))}
        </div>
      </section>

      {/* CTA */}
      <section className="px-6 sm:px-12 md:px-20 lg:px-28 py-20 sm:py-28">
        <div className="max-w-2xl">
          <h2 className="display-l text-[var(--ink-950)] mb-4">
            Trí tuệ robot nên là thứ bạn có thể trò chuyện.
          </h2>
          <p className="body-text text-[var(--ink-950)]/70 mb-8">
            Khám phá cách ngôn ngữ, suy luận, thị giác và hành động kết hợp
            trong một hệ thống robot.
          </p>
          <div className="flex flex-wrap gap-4">
            <Link href="/login" className="btn-primary focus-ring">
              Khám phá nền tảng
            </Link>
            <Link href="/contact" className="btn-secondary focus-ring">
              Liên hệ với đội ngũ
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}
