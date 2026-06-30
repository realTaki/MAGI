/**
 * Landing page — the deployer's first stop.
 *
 * Two CTAs depending on whether the system has been set up:
 *   - "Set up"        — first time (no bot saved); goes to onboarding
 *   - "Sign in"        — already configured; goes to login
 */
export default function LandingPage(props: {
  isFirstTime: boolean;
  onSignIn: () => void;
}) {
  return (
    <main className="min-h-screen flex flex-col px-6 py-16">
      <div className="flex-1 flex items-center justify-center">
        <div className="w-full max-w-xl text-center">
          <div className="inline-flex items-center gap-3 mb-8">
            <img
              src="/assets/favicon.svg"
              alt="MAGI"
              width={40}
              height={40}
              className="rounded"
            />
            <span className="text-2xl font-semibold tracking-wide text-slate-800">
              MAGI
            </span>
          </div>

          <h1 className="text-3xl font-semibold tracking-tight text-slate-800">
            Enterprise agents, on your terms.
          </h1>
          <p className="mt-4 text-slate-600 leading-relaxed">
            MAGI gives every employee a personal agent — running on the
            messaging platform they already use — while keeping the
            infrastructure, skills and audit log under your control.
          </p>

          <div className="mt-10 flex items-center justify-center gap-3">
            <button
              type="button"
              onClick={props.onSignIn}
              className="rounded-md bg-sky-700 text-white px-6 py-2.5 text-sm font-medium shadow-md shadow-sky-700/20 hover:bg-sky-800 transition"
            >
              {props.isFirstTime ? "Set up" : "Sign in"}
            </button>
          </div>

          <p className="mt-6 text-xs text-slate-500">
            {props.isFirstTime
              ? "First time here — Set up runs the first-time wizard."
              : "Enter your Telegram chat_id and the code the bot sends to log in."}
          </p>
        </div>
      </div>
    </main>
  );
}