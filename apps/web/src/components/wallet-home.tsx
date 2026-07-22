import Link from "next/link";
import { ArrowLeft, Coins, LockKeyhole, WalletCards } from "lucide-react";

const ACCOUNT_METRICS = [
  { label: "可用积分", value: "—" },
  { label: "月度免费额度", value: "—" },
  { label: "账户状态", value: "未连接" },
] as const;

export function WalletHome() {
  return (
    <main className="min-h-screen bg-[#f7f5f0] px-5 py-8 text-stone-950 sm:px-8">
      <div className="mx-auto max-w-4xl">
        <Link
          href="/home"
          className="inline-flex items-center gap-2 text-sm font-semibold text-stone-600 hover:text-stone-950"
        >
          <ArrowLeft className="h-4 w-4" />
          返回主页
        </Link>

        <div className="mt-8 flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.25em] text-stone-400">
              OpenClass Credits
            </p>
            <h1 className="mt-2 text-3xl font-semibold">积分与充值</h1>
            <p className="mt-2 text-sm text-stone-500">
              查看模型调用额度，并进入部署方提供的充值服务。
            </p>
          </div>
          <Coins className="h-10 w-10 text-amber-500" />
        </div>

        <section className="mt-8 grid gap-4 sm:grid-cols-3">
          {ACCOUNT_METRICS.map((metric) => (
            <div
              key={metric.label}
              className="rounded-2xl border border-stone-200 bg-white p-5 shadow-sm"
            >
              <p className="text-xs font-semibold text-stone-500">{metric.label}</p>
              <p className="mt-2 text-2xl font-semibold">{metric.value}</p>
            </div>
          ))}
        </section>

        <section className="mt-6 rounded-2xl border border-emerald-200 bg-emerald-50 p-6">
          <div className="flex items-start gap-3">
            <WalletCards className="mt-0.5 h-5 w-5 shrink-0 text-emerald-700" />
            <div>
              <h2 className="font-semibold text-emerald-950">充值服务接入点</h2>
              <p className="mt-2 text-sm leading-6 text-emerald-900">
                当前公开版本保留完整界面入口。余额、交易、支付与用户账户数据由部署方的私有账户服务提供。
              </p>
              <button
                type="button"
                disabled
                className="mt-4 inline-flex items-center gap-2 rounded-lg bg-emerald-700 px-4 py-2.5 text-sm font-semibold text-white opacity-50"
              >
                <LockKeyhole className="h-4 w-4" />
                连接账户服务后可用
              </button>
            </div>
          </div>
        </section>

        <section className="mt-6 rounded-2xl border border-stone-200 bg-white p-6 shadow-sm">
          <h2 className="font-semibold">交易明细</h2>
          <p className="mt-4 rounded-xl border border-dashed border-stone-200 px-4 py-8 text-center text-sm text-stone-500">
            连接账户服务后显示余额与交易记录。
          </p>
        </section>
      </div>
    </main>
  );
}
