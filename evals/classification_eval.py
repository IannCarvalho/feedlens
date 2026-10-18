"""Mede a classificação de feedbacks com a cadeia de provedores configurada.

    python -m evals.classification_eval --set dev
    python -m evals.classification_eval --set holdout --runs 2
    python -m evals.classification_eval --prompt-file outro_prompt.txt

Chama a LLM de verdade (com a chave do servidor ou do cofre), então GASTA
cota: ~1 chamada por caso e por repetição. Para a ferramenta de ajuste do
prompt, não para o CI. Casos em ``evals/cases.py``.

Falha do provedor (timeout, cota...) cai no resultado seguro padrão do
analisador (``analyzed_by`` vazio); esses casos são contados à parte, e não
como acerto nem erro de classificação.
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from evals.cases import SETS, Case


def run_case(app, analyzer, case: Case) -> dict:
    with app.app_context():
        result = analyzer.analyze_feedback(case.text)
    return {"case": case, "result": result}


def judge(case: Case, result: dict) -> dict:
    """Compara a resposta com o esperado. Retorna os erros por dimensão."""
    errors = []
    if result["is_spam"] != case.spam:
        errors.append("spam")
    # Em spam o sentimento não importa; sem sentimento esperado, não se pontua.
    if not case.spam and case.sentiment and result["sentiment"] != case.sentiment:
        errors.append("sentimento")
    has_features = bool(result["requested_features"])
    if case.features is not None and has_features != case.features:
        errors.append("funcionalidades")
    return {"errors": errors}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--set", choices=sorted(SETS), default="dev")
    parser.add_argument("--runs", type=int, default=1, help="repetições de cada caso")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--prompt-file", help="usa este texto como prompt de sistema")
    parser.add_argument("--out", help="grava o resultado detalhado em JSON")
    args = parser.parse_args(argv)

    from app import create_app
    from app.services import feedback_analyzer as module

    if args.prompt_file:
        module.ANALYSIS_SYSTEM_PROMPT = Path(args.prompt_file).read_text(encoding="utf-8").strip()

    app = create_app()
    analyzer = module.FeedbackAnalyzer()
    cases = [case for case in SETS[args.set] for _ in range(args.runs)]

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        outcomes = list(pool.map(lambda c: run_case(app, analyzer, c), cases))

    scored = failed = 0
    misses: dict[str, int] = {"spam": 0, "sentimento": 0, "funcionalidades": 0}
    wrong = []
    rows = []
    for outcome in outcomes:
        case, result = outcome["case"], outcome["result"]
        if result.get("analyzed_by") is None:
            failed += 1
            rows.append({"id": case.id, "status": "falha do provedor"})
            continue
        scored += 1
        verdict = judge(case, result)
        for error in verdict["errors"]:
            misses[error] += 1
        if verdict["errors"]:
            wrong.append(
                f"  {case.id} [{', '.join(verdict['errors'])}] "
                f"sentimento={result['sentiment']} spam={result['is_spam']} "
                f"funcionalidades={len(result['requested_features'])} :: {case.text[:60]!r}")
        rows.append({
            "id": case.id, "status": "ok", "erros": verdict["errors"],
            "sentiment": result["sentiment"], "is_spam": result["is_spam"],
            "features": [f["code"] for f in result["requested_features"]],
        })

    exact = sum(1 for r in rows if r["status"] == "ok" and not r["erros"])
    print(f"conjunto={args.set} execuções={len(outcomes)} avaliadas={scored} "
          f"falhas do provedor={failed}")
    if scored:
        print(f"acertos totais: {exact}/{scored} ({100 * exact / scored:.0f}%)")
        print(f"erros -> spam: {misses['spam']} | sentimento: {misses['sentimento']} "
              f"| funcionalidades: {misses['funcionalidades']}")
    if wrong:
        print("divergências:")
        print("\n".join(wrong))
    if args.out:
        Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
