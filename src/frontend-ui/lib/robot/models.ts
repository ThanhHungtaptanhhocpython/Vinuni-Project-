/* Danh mục loại cánh tay mà hệ thống nói chuyện được.

   Đây là NGUỒN DUY NHẤT của danh sách này: hộp thoại "Thêm robot" dựng lựa
   chọn từ đây, thẻ robot đọc nhãn từ đây, và khi hệ thống hỗ trợ con thứ ba
   thì chỉ file này đổi. Không component nào được tự viết lại chuỗi
   "SO-101" hay "Franka Panda". */

export type RobotModelId = "franka" | "so101";

export interface RobotModelOption {
  id: RobotModelId;
  /** Nhãn hiển thị cho người vận hành. */
  label: string;
  /** Một câu nói rõ đây là cánh tay nào — người chọn không phải đoán. */
  description: string;
}

export const ROBOT_MODELS: RobotModelOption[] = [
  {
    id: "so101",
    label: "SO-101",
    description: "Cánh tay 6 bậc tự do, servo Feetech — loại đang chạy trong lab.",
  },
  {
    id: "franka",
    label: "Franka Panda",
    description: "Cánh tay 7 bậc tự do, điều khiển qua FCI.",
  },
];

const BY_ID = new Map(ROBOT_MODELS.map((m) => [m.id, m]));

/** Nhãn của một loại tay. Backend có thể trả về chuỗi model khác (robot ghép
    nối từ trước, hoặc đời máy mới hơn danh mục này) — khi đó hiện y nguyên
    chuỗi nó trả về chứ không nuốt mất thông tin. */
export function robotModelLabel(model: string): string {
  return BY_ID.get(model as RobotModelId)?.label ?? model;
}

/** ID robot do người dùng nhập: chữ thường, số, gạch ngang, gạch dưới. Ràng
    buộc này để ID còn dùng được trong URL và khoá SSE mà không phải escape. */
export const ROBOT_ID_PATTERN = /^[a-zA-Z0-9][a-zA-Z0-9_-]{2,63}$/;

export const ROBOT_ID_HINT =
  "ID gồm 3–64 ký tự chữ, số, gạch ngang hoặc gạch dưới — trùng với ID in trên thiết bị Edge.";
