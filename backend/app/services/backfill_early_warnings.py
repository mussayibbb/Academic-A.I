from backend.app.database import SessionLocal
from backend.app.main import predict_with_active_model, selected_model_artifact
from backend.app.services.early_warning_snapshots import backfill_weekly_snapshots
from ai.risk.predict import load_artifact


def main() -> None:
    with SessionLocal() as db:
        artifact = load_artifact(selected_model_artifact(db))
        if artifact.get('metadata', {}).get('demo_data'):
            raise SystemExit('Refusing to backfill weekly risk snapshots from a DEMO DATA model.')
        count = backfill_weekly_snapshots(db, lambda features: predict_with_active_model(features, db))
        print(f'Backfilled {count} weekly risk snapshots.')


if __name__ == '__main__':
    main()
